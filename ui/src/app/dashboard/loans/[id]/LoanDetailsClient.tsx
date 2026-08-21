"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loan } from "@/lib/loans";
import { Account } from "@/lib/accounts";
import { disburseLoanAction, repayLoanAction } from "@/actions/loans";
import { 
  Landmark, 
  Calendar, 
  DollarSign, 
  CheckCircle2, 
  Clock, 
  ArrowRight, 
  Coins, 
  Info,
  Loader2,
  AlertCircle
} from "lucide-react";
import GlassCard from "@/components/GlassCard";
import SubmitButton from "@/components/SubmitButton";

interface LoanDetailsClientProps {
  loan: Loan;
  depositAccounts: Account[];
  loanAccount: Account | null;
}

export default function LoanDetailsClient({ loan, depositAccounts, loanAccount }: LoanDetailsClientProps) {
  const router = useRouter();
  
  // Interaction states
  const [isDisbursing, setIsDisbursing] = useState(false);
  const [isRepaying, setIsRepaying] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Repayment form states
  const [fundingAccountId, setFundingAccountId] = useState(depositAccounts[0]?.account_id || "");
  const [repayAmount, setRepayAmount] = useState(parseFloat(loan.monthly_payment).toFixed(2));

  // Determine current loan details
  const isActive = loan.status === "active";
  const isPending = loan.status === "pending_disbursement";
  const isClosed = loan.status === "closed";

  // Balance on the backed loan account
  const loanAccountBalance = loanAccount ? parseFloat(loanAccount.balance) : 0;
  const remainingDebt = isActive ? -loanAccountBalance : isClosed ? 0 : parseFloat(loan.principal_amount);

  const selectedAccount = depositAccounts.find(a => a.account_id === fundingAccountId);
  const selectedBalance = selectedAccount ? parseFloat(selectedAccount.available_balance) : 0;

  const formatCurrency = (val: number) => {
    return new Intl.NumberFormat("en-CA", {
      style: "currency",
      currency: "CAD",
    }).format(val);
  };

  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleDateString("en-CA", {
        year: "numeric",
        month: "long",
        day: "numeric",
        timeZone: "UTC"
      });
    } catch {
      return dateStr;
    }
  };

  const handleDisburse = async () => {
    setIsDisbursing(true);
    setMessage(null);
    try {
      const res = await disburseLoanAction(loan.loan_id);
      if (res.success) {
        setMessage({ type: "success", text: res.message });
        router.refresh();
      } else {
        setMessage({ type: "error", text: res.message });
      }
    } catch (err) {
      console.error(err);
      setMessage({ type: "error", text: "Something went wrong. Please try again." });
    } finally {
      setIsDisbursing(false);
    }
  };

  const handleRepay = async (e: React.FormEvent) => {
    e.preventDefault();
    const amount = parseFloat(repayAmount);
    
    if (isNaN(amount) || amount <= 0) {
      setMessage({ type: "error", text: "Please enter a valid positive repayment amount." });
      return;
    }

    if (amount > selectedBalance) {
      setMessage({ type: "error", text: `Insufficient funds. Your selected account only has ${formatCurrency(selectedBalance)}.` });
      return;
    }

    setIsRepaying(true);
    setMessage(null);
    try {
      const res = await repayLoanAction(loan.loan_id, fundingAccountId, amount);
      if (res.success) {
        setMessage({ type: "success", text: `Successfully paid ${formatCurrency(amount)}! Your loan has been updated.` });
        router.refresh();
      } else {
        setMessage({ type: "error", text: res.message });
      }
    } catch (err) {
      console.error(err);
      setMessage({ type: "error", text: "Something went wrong. Please try again." });
    } finally {
      setIsRepaying(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Alert Messages */}
      {message && (
        <div className={`p-4 rounded-xl border flex items-start gap-3 text-sm ${
          message.type === "success" 
            ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300" 
            : "border-rose-500/20 bg-rose-500/10 text-rose-300"
        }`}>
          {message.type === "success" ? (
             <CheckCircle2 className="w-5 h-5 flex-shrink-0" />
          ) : (
             <AlertCircle className="w-5 h-5 flex-shrink-0" />
          )}
          <div>{message.text}</div>
        </div>
      )}

      {/* Main Loan Metrics and Summary */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Left Card: Status & Quick Info */}
        <GlassCard className="!p-6 flex flex-col justify-between h-full">
          <div>
            <div className="flex items-center gap-3 mb-4">
              <div className={`p-3 rounded-lg ${
                isActive 
                  ? "bg-violet-500/10 text-violet-400" 
                  : isPending 
                    ? "bg-amber-500/10 text-amber-400 animate-pulse" 
                    : "bg-slate-500/10 text-slate-400"
              }`}>
                <Landmark className="w-6 h-6" />
              </div>
              <div>
                <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider">Loan Product</span>
                <h3 className="text-xl font-black text-white">{formatCurrency(parseFloat(loan.principal_amount))} Loan</h3>
              </div>
            </div>

            <div className="space-y-3.5 border-t border-white/5 pt-4 text-sm">
              <div className="flex justify-between">
                <span className="text-slate-400">Status</span>
                <span className={`text-xs px-2.5 py-0.5 rounded-full font-bold uppercase tracking-wider ${
                  isActive 
                    ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/10" 
                    : isPending 
                      ? "bg-amber-500/10 text-amber-400 border border-amber-500/10 animate-pulse" 
                      : "bg-slate-800 text-slate-400 border border-slate-700"
                }`}>
                  {loan.status.replace("_", " ")}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Interest Rate (APR)</span>
                <span className="font-extrabold text-white font-mono">
                  {(parseFloat(loan.interest_rate) * 100).toFixed(2)}%
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Term Period</span>
                <span className="font-bold text-white">
                  {loan.amortization_months} Months
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Date Applied</span>
                <span className="font-medium text-white">
                  {formatDate(loan.created_at)}
                </span>
              </div>
            </div>
          </div>
        </GlassCard>

        {/* Right Card: Financial State */}
        <GlassCard className="!p-6 flex flex-col justify-between h-full">
          <div>
            <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider block">
              {isActive ? "Outstanding Remaining Debt" : isPending ? "Disbursement Amount" : "Loan Balance"}
            </span>
            <h2 className={`text-4xl font-black mt-2 font-mono ${
              isActive 
                ? "text-violet-400" 
                : isPending 
                  ? "text-amber-400" 
                  : "text-slate-400"
            }`}>
              {formatCurrency(remainingDebt)}
            </h2>

            <div className="space-y-3.5 border-t border-white/5 pt-4 text-sm mt-6">
              <div className="flex justify-between">
                <span className="text-slate-400">Monthly Installment</span>
                <span className="font-extrabold text-white">
                  {formatCurrency(parseFloat(loan.monthly_payment))}
                </span>
              </div>
              {isActive && (
                <div className="flex justify-between">
                  <span className="text-slate-400">Next Payment Due</span>
                  <span className="font-bold text-violet-400 font-mono flex items-center gap-1">
                    <Calendar className="w-3.5 h-3.5" />
                    {formatDate(loan.next_payment_date)}
                  </span>
                </div>
              )}
              {isClosed && (
                <div className="flex justify-between items-center text-emerald-400 bg-emerald-500/5 px-2.5 py-1.5 rounded-lg border border-emerald-500/10">
                  <span className="text-xs font-bold inline-flex items-center gap-1">
                    <CheckCircle2 className="w-4 h-4" />
                    Paid Off & Closed
                  </span>
                  <span className="text-xs font-mono">Completed</span>
                </div>
              )}
            </div>
          </div>
        </GlassCard>
      </div>

      {/* Action Hub Panel */}
      {isPending && (
        <GlassCard className="!p-6 border-amber-500/20 bg-amber-500/5">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-6">
            <div className="space-y-1 max-w-lg">
              <h4 className="font-extrabold text-white text-base inline-flex items-center gap-1.5 text-amber-400">
                <Clock className="w-5 h-5" />
                Loan Approved - Awaiting Disbursement
              </h4>
              <p className="text-slate-400 text-xs">
                Your application has been approved. Clicking below will instantly deposit the funds of <strong className="text-white">{formatCurrency(parseFloat(loan.principal_amount))}</strong> into your primary chequing or savings account, and activate daily interest tracking.
              </p>
            </div>
            <button
              onClick={handleDisburse}
              disabled={isDisbursing}
              className="inline-flex items-center justify-center gap-2 px-6 py-3 rounded-lg bg-amber-500 text-black font-extrabold text-sm hover:bg-amber-400 active:scale-95 disabled:opacity-50 disabled:pointer-events-none transition-all cursor-pointer self-start md:self-auto min-w-[180px]"
            >
              {isDisbursing ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Disbursing...
                </>
              ) : (
                <>
                  Disburse Funds
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </div>
        </GlassCard>
      )}

      {isActive && (
        <GlassCard className="!p-6">
          <h4 className="font-extrabold text-white text-base mb-4 inline-flex items-center gap-1.5 text-violet-400 border-b border-white/5 pb-2 w-full">
            <Coins className="w-5 h-5" />
            Make a Loan Repayment
          </h4>

          {depositAccounts.length === 0 ? (
            <div className="p-4 rounded-lg bg-slate-950/40 text-slate-400 text-xs text-center border border-dashed border-slate-700">
              You do not have any active deposit accounts (chequing/savings) to fund this repayment.
            </div>
          ) : (
            <form onSubmit={handleRepay} className="space-y-4 max-w-xl">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Funding Account Selection */}
                <div>
                  <label htmlFor="fundingAccount" className="block text-[11px] text-slate-400 font-semibold uppercase tracking-wider mb-1.5">
                    Funding Account
                  </label>
                  <select
                    id="fundingAccount"
                    value={fundingAccountId}
                    onChange={(e) => setFundingAccountId(e.target.value)}
                    className="w-full bg-slate-950 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-violet-500/50 transition-colors cursor-pointer"
                  >
                    {depositAccounts.map((account) => (
                      <option key={account.account_id} value={account.account_id}>
                        {account.account_type.toUpperCase()} ({account.account_number.slice(-4)}) - {formatCurrency(parseFloat(account.available_balance))}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Repayment Amount */}
                <div>
                  <label htmlFor="repayAmount" className="block text-[11px] text-slate-400 font-semibold uppercase tracking-wider mb-1.5">
                    Repayment Amount ($ CAD)
                  </label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm font-bold">$</span>
                    <input
                      id="repayAmount"
                      type="number"
                      step="0.01"
                      min="0.01"
                      value={repayAmount}
                      onChange={(e) => setRepayAmount(e.target.value)}
                      className="w-full bg-slate-950 border border-white/10 rounded-lg pl-7 pr-3 py-2.5 text-sm text-white focus:outline-none focus:border-violet-500/50 transition-colors font-mono"
                      placeholder="0.00"
                      required
                    />
                  </div>
                </div>
              </div>

              {/* Quick Preset Buttons */}
              <div className="flex gap-2 flex-wrap">
                <button
                  type="button"
                  onClick={() => setRepayAmount(parseFloat(loan.monthly_payment).toFixed(2))}
                  className="px-2.5 py-1 rounded bg-slate-950 border border-white/5 hover:border-violet-500/30 text-[10px] text-slate-400 hover:text-violet-400 transition-colors cursor-pointer font-bold"
                >
                  Monthly Payment ({formatCurrency(parseFloat(loan.monthly_payment))})
                </button>
                <button
                  type="button"
                  onClick={() => setRepayAmount(remainingDebt.toFixed(2))}
                  className="px-2.5 py-1 rounded bg-slate-950 border border-white/5 hover:border-violet-500/30 text-[10px] text-slate-400 hover:text-violet-400 transition-colors cursor-pointer font-bold"
                >
                  Payoff Balance ({formatCurrency(remainingDebt)})
                </button>
              </div>

              {/* Repay Submit Button */}
              <div className="pt-2 flex justify-end">
                <button
                  type="submit"
                  disabled={isRepaying}
                  className="inline-flex items-center justify-center gap-2 px-6 py-2.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-extrabold text-xs active:scale-95 disabled:opacity-50 disabled:pointer-events-none transition-all cursor-pointer min-w-[140px]"
                >
                  {isRepaying ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      Paying...
                    </>
                  ) : (
                    "Make Payment"
                  )}
                </button>
              </div>
            </form>
          )}
        </GlassCard>
      )}

      {/* Helpful Lending Disclosure Info */}
      <GlassCard className="!p-5 border-slate-800 bg-slate-950/20 text-slate-500 text-xs flex gap-3">
        <Info className="w-5 h-5 text-slate-600 flex-shrink-0" />
        <div className="space-y-1">
          <p className="font-semibold text-slate-400">Lending Terms and Conditions</p>
          <p>
            This loan has an annual percentage rate (APR) of {(parseFloat(loan.interest_rate) * 100).toFixed(2)}%, which accrues daily on the remaining outstanding principal. Repayments are applied immediately to lower your outstanding balance. Overpayments are automatically capped to the remaining outstanding balance to prevent overcharging your account.
          </p>
        </div>
      </GlassCard>
    </div>
  );
}
