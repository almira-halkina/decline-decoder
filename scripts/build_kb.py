"""Build backend/data/decline_codes.json from Stripe's official decline-code docs.

Run:  uv run --project backend python scripts/build_kb.py
      (or pass --from-file path/to/codes.md to build from a saved copy)

Where every field comes from
----------------------------
Source: https://docs.stripe.com/declines/codes.md?locale=en-US, section "Card decline codes"
(the ".md" variant is Stripe's own markdown rendering of the page; "locale=en-US" is needed
because docs.stripe.com otherwise localises by geo-IP).

  code          verbatim, column 1 ("Decline code"). "(Deprecated)" prefix is stripped.
  deprecated    true if column 1 carries the "(Deprecated)" prefix.
  description   column 2 ("Description"), with markdown links and glossary pop-ups removed.
  next_steps    column 3 ("Next steps"), cleaned the same way.

Derived fields (rules below are the ONLY interpretation layer; everything else is verbatim):

  conceal_reason        true if the docs' next steps tell you to present the decline to the
                        customer as `generic_decline` (fraudulent, lost_card, merchant_blacklist,
                        stolen_card), OR the code is in PROJECT_CONCEAL (pickup_card,
                        restricted_card: the docs' description says the card may have been
                        reported lost or stolen, so this project chooses not to reveal the reason).
  conceal_basis         "docs" | "project_policy" | null — which of the two rules applied.
  retry_policy          how retrying could help, derived from the next-steps wording:
                          "same_details"           next steps say to attempt the payment again
                                                   as-is (processing_error, issuer_not_available…)
                          "after_customer_action"  retry only after the customer fixes details
                                                   or authenticates (incorrect_cvc, 3DS, PIN…)
                          "no"                     contact issuer / use another card / anything else,
                                                   and every concealed (fraud-related) code.
  retry_basis           the phrase that triggered the rule, so each value is auditable.

The "Local payment method decline codes" table is intentionally excluded: the demo only takes
card payments. Codes outside the knowledge base hit the guardrail fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SOURCE_URL = "https://docs.stripe.com/declines/codes.md?locale=en-US"
OUT_PATH = Path(__file__).resolve().parents[1] / "backend" / "data" / "decline_codes.json"

PROJECT_CONCEAL = {"pickup_card", "restricted_card"}

# First matching rule wins. Patterns run against the lower-cased next-steps text.
RETRY_RULES: list[tuple[str, str]] = [
    ("same_details", r"^(ask the customer to )?attempt the payment again"),
    ("same_details", r"^the payment needs to be attempted again"),
    ("after_customer_action", r"try again (using the correct|by inserting)"),
    ("after_customer_action", r"retry the payment by tapping"),
    ("after_customer_action", r"authenticat|3d secure"),
]

DOCS_CONCEAL_PATTERN = re.compile(r"present it (in the same manner )?as `generic_decline`")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed https URL)
        return str(resp.read().decode("utf-8"))


def clean(cell: str) -> str:
    """Strip markdown links, glossary pop-ups and emphasis from a table cell."""
    text = cell.strip()
    # Glossary pop-ups render as "*term* (long definition with (nested) parens)".
    text = re.sub(r"\*([^*]+)\* \((?:[^()]|\([^()]*\))*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [label](url) -> label
    text = text.replace("&nbsp;", " ").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def card_table_rows(markdown: str) -> list[list[str]]:
    match = re.search(r"^## Card decline codes.*?$(.*?)^## ", markdown, re.S | re.M)
    if not match:
        sys.exit("Could not find the 'Card decline codes' section — page layout changed?")
    rows = []
    for line in match.group(1).splitlines():
        if not line.startswith("|") or line.startswith("| ---") or "Decline code" in line:
            continue
        cells = [c for c in line.strip().strip("|").split(" | ")]
        if len(cells) != 3:
            sys.exit(f"Unexpected row shape ({len(cells)} cells): {line[:80]}")
        rows.append(cells)
    return rows


def derive_retry(next_steps: str, conceal: bool) -> tuple[str, str]:
    if conceal:
        return "no", "concealed (fraud-related) decline"
    lowered = next_steps.lower()
    for policy, pattern in RETRY_RULES:
        if m := re.search(pattern, lowered):
            return policy, m.group(0)
    return "no", "no retry wording in next steps"


def build(markdown: str) -> dict[str, object]:
    codes: dict[str, dict[str, object]] = {}
    for raw_code, raw_desc, raw_next in card_table_rows(markdown):
        deprecated = raw_code.strip().startswith("(Deprecated)")
        code = re.sub(r"[()`]|Deprecated", "", raw_code).strip()
        description, next_steps = clean(raw_desc), clean(raw_next)

        if DOCS_CONCEAL_PATTERN.search(raw_next):
            conceal_basis: str | None = "docs"
        elif code in PROJECT_CONCEAL:
            conceal_basis = "project_policy"
        else:
            conceal_basis = None
        retry_policy, retry_basis = derive_retry(next_steps, conceal_basis is not None)

        codes[code] = {
            "code": code,
            "deprecated": deprecated,
            "description": description,
            "next_steps": next_steps,
            "conceal_reason": conceal_basis is not None,
            "conceal_basis": conceal_basis,
            "retry_policy": retry_policy,
            "retry_basis": retry_basis,
            "doc_url": f"https://docs.stripe.com/declines/codes#{code}",
        }
    return {
        "source_url": SOURCE_URL,
        "source_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
        "built_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "codes": dict(sorted(codes.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-file", type=Path, help="build from a saved copy of codes.md")
    args = parser.parse_args()

    markdown = args.from_file.read_text(encoding="utf-8") if args.from_file else fetch(SOURCE_URL)
    if not markdown.lstrip().startswith("# Stripe decline codes"):
        sys.exit("Page is not the English decline-codes page; refusing to build.")
    kb = build(markdown)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(kb, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    codes = kb["codes"]
    assert isinstance(codes, dict)
    concealed = sorted(c for c, e in codes.items() if e["conceal_reason"])
    print(f"Wrote {len(codes)} codes to {OUT_PATH}")
    print(f"Concealed from customers: {', '.join(concealed)}")
    for policy in ("same_details", "after_customer_action"):
        hits = sorted(c for c, e in codes.items() if e["retry_policy"] == policy)
        print(f"retry_policy={policy}: {', '.join(hits)}")


if __name__ == "__main__":
    main()
