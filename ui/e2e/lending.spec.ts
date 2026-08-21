import { test, expect, type Page } from "@playwright/test";
import { Client } from "pg";

let seq = 0;
function uniqueSuffix() {
  seq += 1;
  return `${Date.now()}${seq}`;
}

function uniqueEmail() {
  return `e2e_lending+${uniqueSuffix()}@example.com`;
}

function uniquePhone() {
  return uniqueSuffix().slice(-10).padStart(10, "0");
}

const PASSWORD = "password123";

// Helper to update KYC status in Postgres
async function verifyKycInDb(email: string) {
  if (process.env.DATABASE_URL) {
    const client = new Client({ connectionString: process.env.DATABASE_URL });
    await client.connect();
    await client.query("UPDATE customers SET kyc_status = 'verified' WHERE email = $1", [email]);
    await client.end();
    return;
  }

  // Resolve credentials from environment variables, fallback to local dev defaults
  const pgUser = process.env.PGUSER || "nanobank_user";
  const pgPassword = process.env.PGPASSWORD || "secure_nano_password_2024!";
  const pgDatabase = process.env.PGDATABASE || "nano_bank_db";

  // Attempt connection on port 55432 (local Colima bypass channel)
  try {
    const client55432 = new Client({
      connectionString: `postgres://${pgUser}:${pgPassword}@127.0.0.1:55432/${pgDatabase}`,
    });
    await client55432.connect();
    await client55432.query("UPDATE customers SET kyc_status = 'verified' WHERE email = $1", [email]);
    await client55432.end();
    return;
  } catch (err) {
    // Fallback to standard 5432
    const client5432 = new Client({
      connectionString: `postgres://${pgUser}:${pgPassword}@127.0.0.1:5432/${pgDatabase}`,
    });
    await client5432.connect();
    await client5432.query("UPDATE customers SET kyc_status = 'verified' WHERE email = $1", [email]);
    await client5432.end();
  }
}

async function signUp(page: Page, email: string) {
  await page.goto("/auth/signup");
  await page.getByLabel(/first name/i).fill("Lending");
  await page.getByLabel(/last name/i).fill("TestUser");
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/phone/i).fill(uniquePhone());
  await page.getByLabel(/date of birth/i).fill("1990-01-01");
  await page.getByLabel(/sin/i).fill("123456789");
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /sign up|create/i }).click();
  await page.waitForURL("**/auth/signin");
}

async function signIn(page: Page, email: string) {
  await page.goto("/auth/signin");
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /sign in|log in/i }).click();
  await page.waitForURL("**/dashboard");
}

test("unverified KYC prevents loan application", async ({ page }) => {
  const email = uniqueEmail();
  await signUp(page, email);
  await signIn(page, email);

  // Navigate to Loans page
  await page.goto("/dashboard/loans");
  await expect(page.getByText(/no loans found/i)).toBeVisible();

  // Go to Apply Loan page
  await page.goto("/dashboard/loans/apply");
  
  // Fill application parameters
  await page.getByLabel(/principal amount/i).fill("5000");
  await page.getByLabel(/annual apr/i).fill("5.5");
  await page.getByRole("button", { name: /apply now/i }).click();

  // KYC error should be displayed since the user is not verified
  await expect(page.getByText(/we couldn't process those details/i)).toBeVisible();
});

test("verified KYC allows full loan lifecycle (apply, disburse, repay, close)", async ({ page }) => {
  const email = uniqueEmail();
  await signUp(page, email);

  // Programmatically verify the user's KYC status in the database
  await verifyKycInDb(email);

  await signIn(page, email);

  // 1. Create a chequing account (needed to receive disbursement)
  await page.goto("/dashboard/accounts/create");
  await page.getByText("Chequing").first().click();
  await page.getByRole("button", { name: /open account/i }).click();
  await expect(page.getByText(/ready/i)).toBeVisible();

  // 2. Go to Apply Loan page
  await page.goto("/dashboard/loans/apply");

  // Verify PMT calculation updates dynamically on the client
  await page.getByLabel(/principal amount/i).fill("10000");
  await page.getByLabel(/annual apr/i).fill("12.0");
  await page.getByLabel(/amortization period/i).selectOption("12"); // 12 Months

  // Formula: R = 12% / 12 = 1% = 0.01. PMT = 10000 * 0.01 * (1.01)^12 / ((1.01)^12 - 1) = 888.49
  await expect(page.getByText(/\$888\.49/)).toBeVisible();

  // Submit the loan application
  await page.getByRole("button", { name: /apply now/i }).click();

  // Should navigate to individual loan page
  await page.waitForURL(/\/dashboard\/loans\/[0-9a-f-]+/);
  await expect(page.getByText(/approved - awaiting disbursement/i)).toBeVisible();
  await expect(page.getByText(/\$10,000\.00/).first()).toBeVisible();

  // 3. Disburse the loan
  await page.getByRole("button", { name: /disburse funds/i }).click();
  
  // Status should update to ACTIVE and outstanding debt should reflect
  await expect(page.getByText(/active/i).first()).toBeVisible();
  await expect(page.getByText(/\$10,000\.00/).first()).toBeVisible(); // outstanding debt

  // 4. Pay back a portion ($500.00)
  await page.getByLabel(/repayment amount/i).fill("500");
  await page.getByRole("button", { name: /make payment/i }).click();

  // Balance should update to outstanding balance of $9,500.00
  await expect(page.getByText(/outstanding remaining debt/i)).toBeVisible();
  await expect(page.getByText(/\$9,500\.00/).first()).toBeVisible();

  // 5. Pay off the entire remaining balance to close the loan
  // Select the payoff balance preset button
  await page.getByRole("button", { name: /payoff balance/i }).click();
  await page.getByRole("button", { name: /make payment/i }).click();

  // Loan should update to closed and outstanding debt should be $0.00
  await expect(page.getByText(/closed/i).first()).toBeVisible();
  await expect(page.getByText(/paid off & closed/i)).toBeVisible();
  await expect(page.getByText(/\$0\.00/).first()).toBeVisible();
});
