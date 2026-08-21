import "server-only";

export interface Loan {
  loan_id: string;
  customer_id: string;
  account_id: string;
  principal_amount: string;
  interest_rate: string;
  amortization_months: number;
  monthly_payment: string;
  status: "pending_disbursement" | "active" | "closed";
  next_payment_date: string;
  created_at: string;
  updated_at: string;
}

export interface LoanSummary {
  loan_id: string;
  account_id: string;
  principal_amount: string;
  status: "pending_disbursement" | "active" | "closed";
  next_payment_date: string;
  monthly_payment: string;
}
