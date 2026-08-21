import TokenCountdown from "@/components/TokenCountdown";
import { decodeJwtExpiry } from "@/lib/jwt";
import { requireSession } from "@/lib/session";
import { Metadata } from 'next';
import { CreditCard, PiggyBank, AlertCircle, Landmark } from "lucide-react";
import { API_BASE_URL } from "@/lib/config";
import { Account } from "@/lib/accounts";
import { LoanSummary } from "@/lib/loans";
import GlassCard from "@/components/GlassCard";
import GradientHeading from "@/components/GradientHeading";
import Link from "next/link";

export const metadata: Metadata = {
  title: 'Nano-Bank - Dashboard',
};

export default async function Page() {
    const { accessToken, profile } = await requireSession();
    const tokenExpiry = decodeJwtExpiry(accessToken);

    // Fetch accounts and loans in parallel; a loans-fetch failure is
    // non-critical (loans is a newer, secondary widget) and must not blank out
    // the accounts view, so the two are handled independently.
    let accounts: Account[] = [];
    let loans: LoanSummary[] = [];
    let fetchError = false;

    const [accResult, loansResult] = await Promise.allSettled([
        fetch(`${API_BASE_URL}/api/v1/accounts`, {
            headers: { Authorization: `Bearer ${accessToken}` },
            cache: "no-store",
        }),
        fetch(`${API_BASE_URL}/api/v1/loans`, {
            headers: { Authorization: `Bearer ${accessToken}` },
            cache: "no-store",
        }),
    ]);

    if (accResult.status === "fulfilled" && accResult.value.ok) {
        accounts = await accResult.value.json();
    } else {
        const detail = accResult.status === "fulfilled" ? accResult.value.status : accResult.reason;
        console.error(`Failed to fetch accounts: ${detail}`);
        fetchError = true;
    }

    if (loansResult.status === "fulfilled" && loansResult.value.ok) {
        loans = await loansResult.value.json();
    } else {
        const detail = loansResult.status === "fulfilled" ? loansResult.value.status : loansResult.reason;
        console.error(`Failed to fetch loans: ${detail}`);
    }

    // Filter and aggregate
    const depositAccounts = accounts.filter(
        (a) => a.account_type === "chequing" || a.account_type === "savings"
    );
    const totalDepositMoney = depositAccounts.reduce(
        (sum, a) => sum + parseFloat(a.balance || "0"),
        0
    );

    const creditCardAccounts = accounts.filter(
        (a) => a.account_type === "credit_card"
    );
    const totalUsedBalance = creditCardAccounts.reduce(
        (sum, a) => sum + parseFloat(a.balance || "0"),
        0
    );

    const loanAccounts = accounts.filter(
        (a) => a.account_type === "loan"
    );
    const totalLoanDebt = loanAccounts.reduce(
        (sum, a) => sum - parseFloat(a.balance || "0"),
        0
    );

    const formatCurrency = (val: number) => {
        return new Intl.NumberFormat("en-CA", {
            style: "currency",
            currency: "CAD",
        }).format(val);
    };

    return (
        <main className="relative z-10 flex-1 flex flex-col items-center justify-center gap-6 px-6 py-12">
            {/* Welcome Card */}
            <GlassCard className="max-w-3xl">
                <div className="mb-8">
                    <GradientHeading>Welcome back, {profile.first_name}</GradientHeading>
                    <p className="text-slate-400 text-sm mt-2">{profile.email}</p>
                    {tokenExpiry !== null && (
                        <p className="text-xs mt-2">
                            <TokenCountdown expiresAt={tokenExpiry} />
                        </p>
                    )}
                </div>

                <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 p-8 text-center text-sm text-slate-400">
                    Your account summaries will show below.
                </div>
            </GlassCard>

            {/* Accounts Dashboard Snapshot Card */}
            <GlassCard className="max-w-3xl">
                {fetchError ? (
                    <div className="flex items-center gap-3 p-4 rounded-xl border border-rose-500/20 bg-rose-500/10 text-rose-300 text-sm">
                        <AlertCircle className="w-5 h-5 flex-shrink-0" />
                        <div>
                            <span className="font-semibold">Unable to load metrics.</span> Make sure the API server is running and accessible.
                        </div>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                        {/* Deposit Cash Column */}
                        <Link 
                            href="/dashboard/accounts"
                            className="flex flex-col justify-between p-6 rounded-xl border border-white/5 bg-slate-900/40 hover:border-nanobank-blue-sky/30 transition-all duration-300 group cursor-pointer hover:scale-[1.02] transform"
                        >
                            <div className="flex justify-between items-start mb-4">
                                <div>
                                    <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider">
                                        Total Cash & Savings
                                    </span>
                                    <h3 className="text-2xl font-extrabold text-white mt-1">
                                        {formatCurrency(totalDepositMoney)}
                                    </h3>
                                </div>
                                <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400 group-hover:scale-110 transition-transform">
                                    <PiggyBank className="w-5 h-5" />
                                </div>
                            </div>
                            <div className="text-[11px] text-slate-400 border-t border-white/5 pt-3 flex justify-between items-center">
                                <span>{depositAccounts.length} Active {depositAccounts.length === 1 ? 'account' : 'accounts'}</span>
                                <span className="text-emerald-400 font-medium group-hover:underline text-[10px]">Details &rarr;</span>
                            </div>
                        </Link>

                        {/* Credit Card Column */}
                        <Link 
                            href="/dashboard/credit"
                            className="flex flex-col justify-between p-6 rounded-xl border border-white/5 bg-slate-900/40 hover:border-nanobank-orange-deep/30 transition-all duration-300 group cursor-pointer hover:scale-[1.02] transform"
                        >
                            <div className="flex justify-between items-start mb-4">
                                <div>
                                    <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider">
                                        Total Used Credit
                                    </span>
                                    <h3 className="text-2xl font-extrabold text-white mt-1">
                                        {formatCurrency(totalUsedBalance)}
                                    </h3>
                                </div>
                                <div className="p-2 rounded-lg bg-nanobank-orange-deep/10 text-nanobank-orange-deep group-hover:scale-110 transition-transform">
                                    <CreditCard className="w-5 h-5" />
                                </div>
                            </div>
                            <div className="text-[11px] text-slate-400 border-t border-white/5 pt-3 flex justify-between items-center">
                                <span>{creditCardAccounts.length} Credit {creditCardAccounts.length === 1 ? 'card' : 'cards'}</span>
                                <span className="text-nanobank-orange-deep font-medium group-hover:underline text-[10px]">Details &rarr;</span>
                            </div>
                        </Link>

                        {/* Loans Column */}
                        <Link 
                            href="/dashboard/loans"
                            className="flex flex-col justify-between p-6 rounded-xl border border-white/5 bg-slate-900/40 hover:border-violet-500/30 transition-all duration-300 group cursor-pointer hover:scale-[1.02] transform"
                        >
                            <div className="flex justify-between items-start mb-4">
                                <div>
                                    <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider">
                                        Total Loan Debt
                                    </span>
                                    <h3 className="text-2xl font-extrabold text-white mt-1">
                                        {formatCurrency(totalLoanDebt)}
                                    </h3>
                                </div>
                                <div className="p-2 rounded-lg bg-violet-500/10 text-violet-400 group-hover:scale-110 transition-transform">
                                    <Landmark className="w-5 h-5" />
                                </div>
                            </div>
                            <div className="text-[11px] text-slate-400 border-t border-white/5 pt-3 flex justify-between items-center">
                                <span>{loans.length} Active {loans.length === 1 ? 'loan' : 'loans'}</span>
                                <span className="text-violet-400 font-medium group-hover:underline text-[10px]">Details &rarr;</span>
                            </div>
                        </Link>
                    </div>
                )}
            </GlassCard>
        </main>
    );
}
