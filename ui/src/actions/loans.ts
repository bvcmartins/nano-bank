"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { API_BASE_URL } from "@/lib/config";
import { friendlyErrorMessage, type ApiErrorBody } from "@/lib/errors";

export interface LoanActionResult {
  success: boolean;
  message: string;
  loanId?: string;
}

export async function applyLoanAction(
  principalAmount: number,
  interestRate: number,
  amortizationMonths: number
): Promise<LoanActionResult> {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get("access_token")?.value;
  if (!accessToken) {
    return { success: false, message: "Your session has expired. Please sign in again." };
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/loans`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        principal_amount: principalAmount,
        interest_rate: interestRate,
        amortization_months: amortizationMonths,
      }),
      cache: "no-store",
    });
  } catch (error) {
    console.error("Loan application request failed:", error);
    return { success: false, message: "Unable to reach the server. Please try again." };
  }

  if (!response.ok) {
    let message = "Unable to apply for loan.";
    try {
      const errorBody: ApiErrorBody = await response.json();
      message = friendlyErrorMessage(errorBody, message);
    } catch (error) {
      console.error("Failed to parse loan application error:", error);
    }
    return { success: false, message };
  }

  const result = await response.json();
  revalidatePath("/dashboard");
  revalidatePath("/dashboard/loans");

  return {
    success: true,
    message: "Loan applied successfully!",
    loanId: result.loan_id,
  };
}

export async function disburseLoanAction(loanId: string): Promise<LoanActionResult> {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get("access_token")?.value;
  if (!accessToken) {
    return { success: false, message: "Your session has expired. Please sign in again." };
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/loans/${loanId}/disburse`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
      cache: "no-store",
    });
  } catch (error) {
    console.error("Loan disbursement request failed:", error);
    return { success: false, message: "Unable to reach the server. Please try again." };
  }

  if (!response.ok) {
    let message = "Unable to disburse loan funds.";
    try {
      const errorBody: ApiErrorBody = await response.json();
      message = friendlyErrorMessage(errorBody, message);
    } catch (error) {
      console.error("Failed to parse disbursement error:", error);
    }
    return { success: false, message };
  }

  revalidatePath("/dashboard");
  revalidatePath("/dashboard/loans");
  revalidatePath(`/dashboard/loans/${loanId}`);

  return {
    success: true,
    message: "Loan funds disbursed successfully into your chequing account!",
  };
}

export async function repayLoanAction(
  loanId: string,
  fundingAccountId: string,
  amount: number
): Promise<LoanActionResult> {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get("access_token")?.value;
  if (!accessToken) {
    return { success: false, message: "Your session has expired. Please sign in again." };
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/loans/${loanId}/repay`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        funding_account_id: fundingAccountId,
        amount: amount,
      }),
      cache: "no-store",
    });
  } catch (error) {
    console.error("Loan repayment request failed:", error);
    return { success: false, message: "Unable to reach the server. Please try again." };
  }

  if (!response.ok) {
    let message = "Unable to process repayment.";
    try {
      const errorBody: ApiErrorBody = await response.json();
      message = friendlyErrorMessage(errorBody, message);
    } catch (error) {
      console.error("Failed to parse repayment error:", error);
    }
    return { success: false, message };
  }

  revalidatePath("/dashboard");
  revalidatePath("/dashboard/loans");
  revalidatePath(`/dashboard/loans/${loanId}`);

  return {
    success: true,
    message: "Repay action successful!",
  };
}
