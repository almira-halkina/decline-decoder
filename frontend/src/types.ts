// Mirrors backend/app/schemas.py and backend/app/repository.py.

export type Category =
  | "insufficient_funds"
  | "card_issue"
  | "fraud_suspected"
  | "authentication_required"
  | "processing_error"
  | "other";

export type Action =
  | "retry_later"
  | "ask_new_payment_method"
  | "request_3ds"
  | "contact_issuer"
  | "do_not_retry"
  | "check_integration";

export interface Explanation {
  category: Category;
  merchant_explanation: string;
  recommended_action: Action;
  customer_message: string;
  retry_safe: boolean;
  source_code: string | null;
  grounded: boolean;
  engine: "rules" | "claude" | "none";
  basis: string[];
  flags: string[];
  doc_url: string | null;
}

export interface Failure {
  decline_code: string | null;
  error_code: string | null;
  message: string | null;
  advice_code: string | null;
  payment_method_type: string | null;
  card_brand: string | null;
  card_country: string | null;
  amount: number | null;
  currency: string | null;
}

export interface StoredFailure {
  id: string;
  payment_intent_id: string;
  created_at: string;
  failure: Failure;
  explanation: Explanation;
}
