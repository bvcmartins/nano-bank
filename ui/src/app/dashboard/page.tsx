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
import ChatBox from "@/components/ChatBox";
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
            {/* Welcome + Accounts Summary Card */}
            <GlassCard className="max-w-5xl">
                <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                    <div>
                        <GradientHeading>Welcome back, {profile.first_name}</GradientHeading>
                        {tokenExpiry !== null && (
                            <p className="text-xs mt-2">
                                <TokenCountdown expiresAt={tokenExpiry} />
                            </p>
                        )}
                    </div>

                    {fetchError ? (
                        <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-rose-500/20 bg-rose-500/10 text-rose-300 text-xs">
                            <AlertCircle className="w-4 h-4 flex-shrink-0" />
                            <span className="font-semibold">Unable to load metrics.</span>
                        </div>
                    ) : (
                        <div className="flex flex-wrap gap-3">
                            <Link
                                href="/dashboard/accounts"
                                className="flex items-center gap-4 px-5 py-3.5 rounded-xl border border-white/5 bg-slate-900/40 hover:border-nanobank-blue-sky/30 transition-all duration-300 group"
                            >
                                <div className="p-2.5 rounded-lg bg-emerald-500/10 text-emerald-400 group-hover:scale-110 transition-transform">
                                    <PiggyBank className="w-5 h-5" />
                                </div>
                                <div>
                                    <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider block">
                                        Cash & Savings
                                    </span>
                                    <span className="text-lg font-bold text-white">
                                        {formatCurrency(totalDepositMoney)}
                                    </span>
                                </div>
                            </Link>

                            <Link
                                href="/dashboard/credit"
                                className="flex items-center gap-4 px-5 py-3.5 rounded-xl border border-white/5 bg-slate-900/40 hover:border-nanobank-orange-deep/30 transition-all duration-300 group"
                            >
                                <div className="p-2.5 rounded-lg bg-nanobank-orange-deep/10 text-nanobank-orange-deep group-hover:scale-110 transition-transform">
                                    <CreditCard className="w-5 h-5" />
                                </div>
                                <div>
                                    <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider block">
                                        Used Credit
                                    </span>
                                    <span className="text-lg font-bold text-white">
                                        {formatCurrency(totalUsedBalance)}
                                    </span>
                                </div>
                            </Link>

                            <Link
                                href="/dashboard/loans"
                                className="flex items-center gap-4 px-5 py-3.5 rounded-xl border border-white/5 bg-slate-900/40 hover:border-violet-500/30 transition-all duration-300 group"
                            >
                                <div className="p-2.5 rounded-lg bg-violet-500/10 text-violet-400 group-hover:scale-110 transition-transform">
                                    <Landmark className="w-5 h-5" />
                                </div>
                                <div>
                                    <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider block">
                                        Loan Debt
                                    </span>
                                    <span className="text-lg font-bold text-white">
                                        {formatCurrency(totalLoanDebt)}
                                    </span>
                                </div>
                            </Link>
                        </div>
                    )}
                </div>

            </GlassCard>

            {/* AI Assistant */}
            <GlassCard className="max-w-5xl">
                <ChatBox />
            </GlassCard>
        </main>
    );
}
