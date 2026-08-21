import { requireSession } from "@/lib/session";
import { Metadata } from 'next';
import { Landmark, AlertCircle, Plus, Calendar, Clock, CheckCircle2 } from "lucide-react";
import { API_BASE_URL } from "@/lib/config";
import { Account } from "@/lib/accounts";
import { LoanSummary } from "@/lib/loans";
import BackLink from "@/components/BackLink";
import GlassCard from "@/components/GlassCard";
import GradientHeading from "@/components/GradientHeading";
import Link from "next/link";

export const metadata: Metadata = {
  title: 'Nano-Bank - Loans & Borrowing',
};

export default async function LoansPage() {
    const { accessToken } = await requireSession();

    // Fetch accounts and loans in parallel
    let accounts: Account[] = [];
    let loans: LoanSummary[] = [];
    let fetchError = false;
    try {
        const [accRes, loansRes] = await Promise.all([
            fetch(`${API_BASE_URL}/api/v1/accounts`, {
                headers: { Authorization: `Bearer ${accessToken}` },
                cache: "no-store",
            }),
            fetch(`${API_BASE_URL}/api/v1/loans`, {
                headers: { Authorization: `Bearer ${accessToken}` },
                cache: "no-store",
            })
        ]);

        if (accRes.ok && loansRes.ok) {
            accounts = await accRes.json();
            loans = await loansRes.json();
        } else {
            console.error(`Failed to fetch data. Accounts: ${accRes.status}, Loans: ${loansRes.status}`);
            fetchError = true;
        }
    } catch (error) {
        console.error("Failed to fetch loans:", error);
        fetchError = true;
    }

    // Filter only loan accounts for total debt calculation
    const loanAccounts = accounts.filter(
        (a) => a.account_type === "loan"
    );

    const totalLoanDebt = loanAccounts.reduce(
        (sum, a) => sum - parseFloat(a.balance || "0"),
        0
    );

    const formatCurrency = (val: number | string) => {
        const num = typeof val === "string" ? parseFloat(val) : val;
        return new Intl.NumberFormat("en-CA", {
            style: "currency",
            currency: "CAD",
        }).format(num);
    };

    const formatDate = (dateStr: string) => {
        try {
            return new Date(dateStr).toLocaleDateString("en-CA", {
                year: "numeric",
                month: "long",
                day: "numeric",
                timeZone: "UTC", // API returns UTC NaiveDates
            });
        } catch {
            return dateStr;
        }
    };

    return (
        <main className="relative z-10 flex-1 flex flex-col items-center justify-center px-6 py-12">
            <div className="w-full max-w-3xl">
                <BackLink href="/dashboard">Back to Dashboard</BackLink>

                {/* Main Content Card */}
                <GlassCard>
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8 border-b border-white/10 pb-6">
                        <div>
                            <div className="flex flex-col sm:flex-row sm:items-center gap-4">
                                <GradientHeading>Loans & Borrowing</GradientHeading>
                                <Link
                                    href="/dashboard/loans/apply"
                                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-violet-500/30 bg-violet-500/10 text-violet-400 hover:bg-violet-500/20 transition-all text-xs font-semibold self-start sm:self-center cursor-pointer"
                                >
                                    <Plus className="w-3.5 h-3.5" />
                                    Apply for Loan
                                </Link>
                            </div>
                        </div>
                        <div className="text-right">
                            <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider">
                                Total Outstanding Debt
                            </span>
                            <h2 className="text-3xl font-black text-violet-400 mt-1">
                                {formatCurrency(totalLoanDebt)}
                            </h2>
                        </div>
                    </div>

                    {fetchError ? (
                        <div className="flex items-center gap-3 p-4 rounded-xl border border-rose-500/20 bg-rose-500/10 text-rose-300 text-sm">
                            <AlertCircle className="w-5 h-5 flex-shrink-0" />
                            <div>
                                <span className="font-semibold">Error fetching loans.</span> Make sure the API server is running and accessible.
                            </div>
                        </div>
                    ) : loans.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 p-12 text-center text-sm text-slate-400 flex flex-col items-center justify-center gap-4">
                            <Landmark className="w-10 h-10 text-slate-600" />
                            <div>
                                <p className="font-semibold text-white mb-1">No Loans Found</p>
                                <p className="text-slate-500 max-w-sm mx-auto">You do not have any active or pending loans. Click the button above to apply for competitive interest rates!</p>
                            </div>
                            <Link
                                href="/dashboard/loans/apply"
                                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-violet-500/30 bg-violet-500/10 text-violet-400 hover:bg-violet-500/20 transition-all text-xs font-bold cursor-pointer mt-2"
                            >
                                <Plus className="w-4 h-4" />
                                Apply for a Loan
                            </Link>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {loans.map((loan) => {
                                const isActive = loan.status === "active";
                                const isPending = loan.status === "pending_disbursement";
                                const isClosed = loan.status === "closed";

                                return (
                                    <Link
                                        key={loan.loan_id}
                                        href={`/dashboard/loans/${loan.loan_id}`}
                                        className="block p-5 rounded-xl border border-white/5 bg-slate-900/20 hover:border-violet-500/20 hover:bg-slate-900/40 transition-all duration-300 transform hover:translate-x-1 group"
                                    >
                                        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                                            {/* Left Column: Icon + Primary Info */}
                                            <div className="flex items-start gap-3.5">
                                                <div className={`p-3 rounded-lg flex-shrink-0 ${
                                                    isActive 
                                                        ? "bg-violet-500/10 text-violet-400" 
                                                        : isPending 
                                                            ? "bg-amber-500/10 text-amber-400 animate-pulse" 
                                                            : "bg-slate-500/10 text-slate-400"
                                                }`}>
                                                    <Landmark className="w-5 h-5" />
                                                </div>
                                                <div>
                                                    <div className="flex items-center gap-2.5">
                                                        <h4 className="font-extrabold text-white text-base">
                                                            {formatCurrency(loan.principal_amount)} Loan
                                                        </h4>
                                                        <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider ${
                                                            isActive 
                                                                ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/10" 
                                                                : isPending 
                                                                    ? "bg-amber-500/10 text-amber-400 border border-amber-500/10" 
                                                                    : "bg-slate-800 text-slate-400 border border-slate-700"
                                                        }`}>
                                                            {loan.status.replace("_", " ")}
                                                        </span>
                                                    </div>
                                                    <p className="text-slate-400 text-xs mt-1 font-mono">
                                                        Ref: {loan.loan_id.slice(0, 8).toUpperCase()}...
                                                    </p>
                                                </div>
                                            </div>

                                            {/* Right Column: Values & Actions */}
                                            <div className="flex flex-row sm:flex-col items-center sm:items-end justify-between sm:justify-start gap-2 border-t sm:border-t-0 border-white/5 pt-3 sm:pt-0">
                                                <div className="text-left sm:text-right">
                                                    <p className="text-slate-500 text-[10px] font-semibold uppercase tracking-wider">
                                                        Monthly Payment
                                                    </p>
                                                    <p className="text-sm font-extrabold text-white mt-0.5">
                                                        {formatCurrency(loan.monthly_payment)}
                                                    </p>
                                                </div>

                                                <div className="flex items-center gap-1.5 text-slate-400 text-xs">
                                                    {isPending ? (
                                                        <span className="text-amber-400 font-medium inline-flex items-center gap-1 text-[11px]">
                                                            <Clock className="w-3 h-3" />
                                                            Needs Disbursement &rarr;
                                                        </span>
                                                    ) : isActive ? (
                                                        <span className="text-slate-400 inline-flex items-center gap-1 text-[11px] font-mono">
                                                            <Calendar className="w-3 h-3 text-slate-500" />
                                                            Due: {formatDate(loan.next_payment_date)}
                                                        </span>
                                                    ) : (
                                                        <span className="text-slate-500 inline-flex items-center gap-1 text-[11px]">
                                                            <CheckCircle2 className="w-3 h-3 text-slate-600" />
                                                            Paid off
                                                        </span>
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                    </Link>
                                );
                            })}
                        </div>
                    )}
                </GlassCard>
            </div>
        </main>
    );
}
