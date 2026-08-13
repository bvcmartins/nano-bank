"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Wallet, PiggyBank } from "lucide-react";
import { createAccountAction } from "@/actions/accounts";
import SubmitButton from "@/components/SubmitButton";

const ACCOUNT_TYPES = [
  {
    value: "chequing",
    label: "Chequing",
    description: "Everyday spending, 3.00% interest.",
    icon: Wallet,
  },
  {
    value: "savings",
    label: "Savings",
    description: "Set money aside for later.",
    icon: PiggyBank,
  },
] as const;

export default function CreateAccountForm() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [accountType, setAccountType] = useState<(typeof ACCOUNT_TYPES)[number]["value"]>("chequing");
  // One key for the lifetime of this form mount: a double-click or a retry
  // after a dropped response reuses it, so the server collapses the repeat
  // into the original account instead of opening a second one.
  const [idempotencyKey] = useState(() => crypto.randomUUID());

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoading(true);

    const formData = new FormData(event.currentTarget);
    try {
      const response = await createAccountAction(formData);
      if (response.success) {
        toast.success(response.message);
        router.push(response.accountId ? `/dashboard/accounts/${response.accountId}` : "/dashboard/accounts");
        // Don't clear `loading` here — we're navigating away, and re-enabling
        // the button while that's in flight flashes it back to "Open Account".
        return;
      }
      toast.error(response.message);
    } catch (error) {
      console.error(error);
      toast.error("An unexpected error occurred while opening your account.");
    }
    setLoading(false);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-5 w-full">
      <input type="hidden" name="idempotencyKey" value={idempotencyKey} />
      <fieldset className="space-y-1.5 border-0 p-0 m-0">
        <legend className="text-xs font-semibold tracking-wide text-slate-300 px-0">Account Type</legend>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {ACCOUNT_TYPES.map(({ value, label, description, icon: Icon }) => {
            const selected = accountType === value;
            return (
              <label
                key={value}
                className={`flex items-start gap-3 p-4 rounded-xl border cursor-pointer transition-all focus-within:ring-2 focus-within:ring-nanobank-blue-sky/60 focus-within:ring-offset-2 focus-within:ring-offset-slate-950 ${
                  selected
                    ? "border-nanobank-blue-sky/60 bg-nanobank-blue-sky/10"
                    : "border-slate-700 bg-slate-900/50 hover:border-slate-500"
                }`}
              >
                <input
                  type="radio"
                  name="accountType"
                  value={value}
                  checked={selected}
                  onChange={() => setAccountType(value)}
                  className="sr-only"
                />
                <div
                  className={`p-2 rounded-lg ${
                    selected ? "bg-nanobank-blue-sky/20 text-nanobank-blue-sky" : "bg-slate-800 text-slate-400"
                  }`}
                >
                  <Icon className="w-5 h-5" />
                </div>
                <div>
                  <p className="text-sm font-bold text-white">{label}</p>
                  <p className="text-xs text-slate-400 mt-0.5">{description}</p>
                </div>
              </label>
            );
          })}
        </div>
      </fieldset>

      <SubmitButton loading={loading} loadingText="Opening Account...">
        Open Account
      </SubmitButton>
    </form>
  );
}
