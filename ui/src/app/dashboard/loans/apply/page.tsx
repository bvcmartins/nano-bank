"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { applyLoanAction } from "@/actions/loans";
import { 
  Landmark, 
  ArrowLeft, 
  DollarSign, 
  Percent, 
  Calendar, 
  Loader2, 
  AlertCircle,
  TrendingDown,
  ChevronRight,
  Info
} from "lucide-react";
import BackLink from "@/components/BackLink";
import GlassCard from "@/components/GlassCard";
import GradientHeading from "@/components/GradientHeading";

export default function ApplyLoanPage() {
  const router = useRouter();

  // Form state
  const [principal, setPrincipal] = useState("10000");
  const [ratePercent, setRatePercent] = useState("8.5");
  const [months, setMonths] = useState("24");

  // Interaction states
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Computed state (live PMT preview)
  const [livePmt, setLivePmt] = useState(0);

  // Amortized Payment (PMT) Calculation
  useEffect(() => {
    const P = parseFloat(principal);
    const annualRate = parseFloat(ratePercent) / 100;
    const n = parseInt(months);

    if (isNaN(P) || P <= 0 || isNaN(annualRate) || annualRate < 0 || isNaN(n) || n <= 0) {
      setLivePmt(0);
      return;
    }

    if (annualRate === 0) {
      setLivePmt(P / n);
      return;
    }

    const r = annualRate / 12;
    const pmt = (P * r * Math.pow(1 + r, n)) / (Math.pow(1 + r, n) - 1);
    setLivePmt(pmt);
  }, [principal, ratePercent, months]);

  const formatCurrency = (val: number) => {
    return new Intl.NumberFormat("en-CA", {
      style: "currency",
      currency: "CAD",
    }).format(val);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const P = parseFloat(principal);
    const R = parseFloat(ratePercent) / 100;
    const M = parseInt(months);

    if (isNaN(P) || P <= 0) {
      setError("Principal amount must be a positive number.");
      return;
    }
    if (isNaN(R) || R < 0 || R > 1) {
      setError("Annual interest rate must be between 0% and 100%.");
      return;
    }
    if (isNaN(M) || M <= 0) {
      setError("Amortization months must be a positive integer.");
      return;
    }

    setIsSubmitting(true);
    try {
      const res = await applyLoanAction(P, R, M);
      if (res.success && res.loanId) {
        router.push(`/dashboard/loans/${res.loanId}`);
      } else {
        setError(res.message);
      }
    } catch (err) {
      console.error(err);
      setError("Unable to complete application. Please verify the API is running.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="relative z-10 flex-1 flex flex-col items-center justify-center px-6 py-12">
      <div className="w-full max-w-3xl">
        <BackLink href="/dashboard/loans">Back to Loans</BackLink>

        {/* Form & Calculator Card */}
        <GlassCard>
          <div className="border-b border-white/5 pb-4 mb-6">
            <GradientHeading>Apply for a Loan</GradientHeading>
            <p className="text-slate-400 text-xs mt-1">
              Select your loan parameters below and preview your monthly installments live.
            </p>
          </div>

          {error && (
            <div className="p-4 rounded-xl border border-rose-500/20 bg-rose-500/10 text-rose-300 text-sm flex items-start gap-3 mb-6">
              <AlertCircle className="w-5 h-5 flex-shrink-0" />
              <div>{error}</div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-5 gap-8">
            {/* Left Inputs (3/5 width) */}
            <form onSubmit={handleSubmit} className="space-y-5 md:col-span-3">
              {/* Principal Amount */}
              <div>
                <label htmlFor="principal" className="block text-[11px] text-slate-400 font-semibold uppercase tracking-wider mb-2">
                  Principal Amount ($ CAD)
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 font-bold">$</span>
                  <input
                    id="principal"
                    type="number"
                    min="100"
                    max="100000"
                    step="1"
                    value={principal}
                    onChange={(e) => setPrincipal(e.target.value)}
                    className="w-full bg-slate-950 border border-white/10 rounded-lg pl-8 pr-3 py-3 text-sm text-white focus:outline-none focus:border-violet-500/50 transition-colors font-mono text-base font-bold"
                    placeholder="10,000"
                    required
                  />
                </div>
                <span className="text-[10px] text-slate-500 mt-1 block">
                  Typical loans range from $500 to $100,000.
                </span>
              </div>

              {/* Interest Rate & Term Period in Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* Interest Rate */}
                <div>
                  <label htmlFor="ratePercent" className="block text-[11px] text-slate-400 font-semibold uppercase tracking-wider mb-2">
                    Annual APR (%)
                  </label>
                  <div className="relative">
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 font-bold">%</span>
                    <input
                      id="ratePercent"
                      type="number"
                      step="0.01"
                      min="0"
                      max="100"
                      value={ratePercent}
                      onChange={(e) => setRatePercent(e.target.value)}
                      className="w-full bg-slate-950 border border-white/10 rounded-lg pl-3 pr-8 py-3 text-sm text-white focus:outline-none focus:border-violet-500/50 transition-colors font-mono text-base font-bold"
                      placeholder="8.50"
                      required
                    />
                  </div>
                </div>

                {/* Term Months */}
                <div>
                  <label htmlFor="months" className="block text-[11px] text-slate-400 font-semibold uppercase tracking-wider mb-2">
                    Amortization Period
                  </label>
                  <select
                    id="months"
                    value={months}
                    onChange={(e) => setMonths(e.target.value)}
                    className="w-full bg-slate-950 border border-white/10 rounded-lg px-3 py-3 text-sm text-white focus:outline-none focus:border-violet-500/50 transition-colors cursor-pointer text-base font-bold"
                  >
                    <option value="6">6 Months</option>
                    <option value="12">12 Months (1 Year)</option>
                    <option value="24">24 Months (2 Years)</option>
                    <option value="36">36 Months (3 Years)</option>
                    <option value="48">48 Months (4 Years)</option>
                    <option value="60">60 Months (5 Years)</option>
                  </select>
                </div>
              </div>

              {/* Form Actions */}
              <div className="pt-4 border-t border-white/5 flex items-center justify-between gap-4">
                <span className="text-[10px] text-slate-500 flex items-center gap-1">
                  <Info className="w-3.5 h-3.5" />
                  Subject to instant KYC verification check.
                </span>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="inline-flex items-center justify-center gap-1.5 px-6 py-3 rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-extrabold text-xs active:scale-95 disabled:opacity-50 disabled:pointer-events-none transition-all cursor-pointer min-w-[150px]"
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Applying...
                    </>
                  ) : (
                    <>
                      Apply Now
                      <ChevronRight className="w-4 h-4" />
                    </>
                  )}
                </button>
              </div>
            </form>

            {/* Right Live Preview Box (2/5 width) */}
            <div className="md:col-span-2 flex flex-col justify-between p-6 rounded-xl border border-violet-500/20 bg-violet-500/5">
              <div>
                <span className="text-violet-400 text-xs font-semibold uppercase tracking-wider block mb-2">
                  Live Installment Estimate
                </span>
                <h3 className="text-3xl font-black text-white font-mono">
                  {formatCurrency(livePmt)}
                  <span className="text-xs text-slate-400 font-normal"> / mo</span>
                </h3>
                <p className="text-slate-400 text-[11px] mt-2 leading-relaxed">
                  Calculated based on standard compounding interest metrics. Your actual contract payment will be finalized upon approval.
                </p>
              </div>

              <div className="space-y-3.5 border-t border-white/10 pt-4 mt-6 text-xs">
                <div className="flex justify-between">
                  <span className="text-slate-500">Requested Principal</span>
                  <span className="text-slate-300 font-bold font-mono">
                    {formatCurrency(parseFloat(principal) || 0)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Loan APR</span>
                  <span className="text-slate-300 font-bold font-mono">
                    {parseFloat(ratePercent || "0").toFixed(2)}%
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Total Payments</span>
                  <span className="text-slate-300 font-bold">
                    {months} Monthly Installments
                  </span>
                </div>
                <div className="flex justify-between border-t border-white/5 pt-2 mt-2 text-violet-400 font-semibold">
                  <span className="inline-flex items-center gap-1">
                    <TrendingDown className="w-3.5 h-3.5" />
                    Total Lifetime Cost
                  </span>
                  <span className="font-mono">
                    {formatCurrency(livePmt * parseInt(months) || 0)}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </GlassCard>
      </div>
    </main>
  );
}
