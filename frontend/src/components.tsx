import { useState } from "react";
import { CATEGORY_LABELS } from "./format";
import type { Category } from "./types";

export function CategoryBadge({ category }: { category: Category }) {
  return <span className={`badge badge-${category}`}>{CATEGORY_LABELS[category]}</span>;
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be blocked (e.g. insecure context); the text is still selectable.
    }
  }
  return (
    <button type="button" className="button-secondary button-small" onClick={copy}>
      {copied ? "Copied" : label}
    </button>
  );
}
