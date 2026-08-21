import "server-only";

/** Mirrors the API's `Account` model (api/src/models/account.rs); numeric
 * fields come back as JSON strings (rust_decimal), not numbers. */
export interface Account {
  account_id: string;
  account_number: string;
  account_type: "chequing" | "savings" | "credit_card" | "loan";
  status: "active" | "frozen" | "closed" | "pending_activation";
  balance: string;
  available_balance: string;
  overdraft_limit: string;
}
