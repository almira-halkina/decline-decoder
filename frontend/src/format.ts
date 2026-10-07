import type { Action, Category } from "./types";

export const CATEGORY_LABELS: Record<Category, string> = {
  insufficient_funds: "Insufficient funds",
  card_issue: "Card issue",
  fraud_suspected: "Fraud suspected",
  authentication_required: "Authentication required",
  processing_error: "Processing error",
  other: "Other",
};

export const ACTION_LABELS: Record<Action, string> = {
  retry_later: "Retry later",
  ask_new_payment_method: "Ask for a new payment method",
  request_3ds: "Request 3D Secure",
  contact_issuer: "Customer should contact their bank",
  do_not_retry: "Do not retry",
  check_integration: "Check your integration",
};

export function formatAmount(amount: number | null, currency: string | null): string {
  if (amount === null || currency === null) return "—";
  // Stripe amounts are in the smallest currency unit; Intl knows each currency's decimals.
  const digits = new Intl.NumberFormat("en", { style: "currency", currency }).resolvedOptions()
    .maximumFractionDigits ?? 2;
  return new Intl.NumberFormat("en", { style: "currency", currency }).format(
    amount / 10 ** digits,
  );
}

export function formatTime(iso: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}
