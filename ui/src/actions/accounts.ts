"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { API_BASE_URL } from "@/lib/config";
import { friendlyErrorMessage, type ApiErrorBody } from "@/lib/errors";

/** Mirrors the API's `AccountResponse` (api/src/models/account.rs); numeric
 * fields come back as JSON strings (rust_decimal), not numbers. */
interface AccountResponseBody {
  account_id: string;
  account_number: string;
  account_type: "chequing" | "savings" | "credit_card";
}

export interface CreateAccountResult {
  success: boolean;
  message: string;
  accountId?: string;
}

/** The only account types this form opens — credit cards are opened through a
 * separate flow (see api/src/handlers/accounts.rs `opening_terms`). */
const OPENABLE_ACCOUNT_TYPES = new Set(["chequing", "savings"]);

export async function createAccountAction(formData: FormData): Promise<CreateAccountResult> {
  const accountType = formData.get("accountType");

  if (typeof accountType !== "string" || !OPENABLE_ACCOUNT_TYPES.has(accountType)) {
    return { success: false, message: "Please select an account type." };
  }

  // One key per form mount (see CreateAccountForm) so a double-click or a
  // retry after a dropped response replays the original account instead of
  // opening a second one.
  const idempotencyKey = formData.get("idempotencyKey");

  const cookieStore = await cookies();
  const accessToken = cookieStore.get("access_token")?.value;
  if (!accessToken) {
    return { success: false, message: "Your session has expired. Please sign in again." };
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/accounts`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        account_type: accountType,
        idempotency_key: typeof idempotencyKey === "string" ? idempotencyKey : undefined,
      }),
      cache: "no-store",
    });
  } catch (error) {
    console.error("Account creation request failed:", error);
    return { success: false, message: "Unable to reach the server. Please try again." };
  }

  if (!response.ok) {
    let message = "Unable to open account.";
    try {
      const errorBody: ApiErrorBody = await response.json();
      message = friendlyErrorMessage(errorBody, message);
    } catch (error) {
      console.error("Failed to parse account creation error response:", error);
    }
    return { success: false, message };
  }

  const account: AccountResponseBody = await response.json();
  revalidatePath("/dashboard/accounts");

  return {
    success: true,
    message: `Your new ${accountType} account is ready!`,
    accountId: account.account_id,
  };
}
