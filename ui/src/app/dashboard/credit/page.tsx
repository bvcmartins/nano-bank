import { requireSession } from "@/lib/session";
import { Metadata } from 'next';
import { AlertCircle } from "lucide-react";
import { API_BASE_URL } from "@/lib/config";
import { Account } from "@/lib/accounts";
import BackLink from "@/components/BackLink";
import GlassCard from "@/components/GlassCard";
import GradientHeading from "@/components/GradientHeading";
import Link from "next/link";

export const metadata: Metadata = {
  title: 'Nano-Bank - Credit Cards',
};

export default async function CreditCardsPage() {
    const { accessToken, profile } = await requireSession();

    // Fetch accounts
    let accounts: Account[] = [];
    let fetchError = false;
    try {
        const response = await fetch(`${API_BASE_URL}/api/v1/accounts`, {
            headers: { Authorization: `Bearer ${accessToken}` },
            cache: "no-store",
        });
        if (response.ok) {
            accounts = await response.json();
        } else {
            console.error(`Failed to fetch accounts: ${response.status}`);
            fetchError = true;
        }
    } catch (error) {
        console.error("Failed to fetch accounts:", error);
        fetchError = true;
    }

    // Filter only credit cards
    const creditCardAccounts = accounts.filter(
        (a) => a.account_type === "credit_card"
    );

    // Fetch complete details for each credit card to obtain credit limit and available balance
    let creditCardsDetails: Account[] = [];
    if (!fetchError && creditCardAccounts.length > 0) {
        try {
            const details = await Promise.all(
                creditCardAccounts.map(async (card): Promise<Account | null> => {
                    const response = await fetch(`${API_BASE_URL}/api/v1/accounts/${card.account_id}`, {
                        headers: { Authorization: `Bearer ${accessToken}` },
                        cache: "no-store",
                    });
                    if (response.ok) {
                        return await response.json();
                    }
                    return null;
                })
            );
            creditCardsDetails = details.filter((card): card is Account => card !== null);
        } catch (error) {
            console.error("Failed to fetch card details:", error);
            fetchError = true;
        }
    }

    const totalUsedBalance = creditCardsDetails.reduce(
        (sum, card) => sum + parseFloat(card.balance || "0"),
        0
    );

    const formatCurrency = (val: number) => {
        return new Intl.NumberFormat("en-CA", {
            style: "currency",
            currency: "CAD",
        }).format(val);
    };

    const formatCardNumber = (accountNum: string) => {
        // Obfuscate and show the last 4 digits in card-like format: "•••• •••• •••• 1234"
        const last4 = accountNum.slice(-4) || "0000";
        return `•••• •••• •••• ${last4}`;
    };

    return (
        <main className="relative z-10 flex-1 flex flex-col items-center justify-center px-6 py-12">
            <div className="w-full max-w-3xl">
                <BackLink href="/dashboard">Back to Dashboard</BackLink>

                {/* Main Content Card */}
                <GlassCard>
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8 border-b border-white/10 pb-6">
                        <div>
                            <GradientHeading>Credit Cards</GradientHeading>
                        </div>
                        <div className="text-right">
                            <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider">
                                Total Used Balance
                            </span>
                            <h2 className="text-3xl font-black text-nanobank-orange-deep mt-1">
                                {formatCurrency(totalUsedBalance)}
                            </h2>
                        </div>
                    </div>

                    {fetchError ? (
                        <div className="flex items-center gap-3 p-4 rounded-xl border border-rose-500/20 bg-rose-500/10 text-rose-300 text-sm">
                            <AlertCircle className="w-5 h-5 flex-shrink-0" />
                            <div>
                                <span className="font-semibold">Error retrieving cards.</span>
                            </div>
                        </div>
                    ) : creditCardsDetails.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 p-8 text-center text-sm text-slate-400">
                            You do not have any active credit cards yet.
                        </div>
                    ) : (
                        <div className="space-y-8">
                            {creditCardsDetails.map((card) => {
                                const used = parseFloat(card.balance || "0");
                                const limit = parseFloat(card.overdraft_limit || "0");
                                const available = parseFloat(card.available_balance || "0");
                                const usagePercent = limit > 0 ? Math.min(100, Math.max(0, (used / limit) * 100)) : 0;

                                return (
                                    <Link 
                                        key={card.account_id}
                                        href={`/dashboard/credit/${card.account_id}`}
                                        className="p-6 rounded-xl border border-white/5 bg-slate-900/40 hover:border-white/15 hover:bg-slate-900/60 transition-all duration-300 cursor-pointer hover:scale-[1.01] transform block group"
                                    >
                                        <div className="grid grid-cols-1 md:grid-cols-12 gap-8 items-center">
                                            {/* Left Column: Stylized Visual Credit Card */}
                                            <div className="md:col-span-5 flex justify-center">
                                                <div className="relative w-full max-w-[280px] aspect-[1.586/1] rounded-xl p-4 bg-gradient-to-br from-white/15 to-white/5 border border-white/10 shadow-lg select-none group-hover:scale-[1.02] transition-transform duration-300">
                                                    <div className="h-full flex flex-col justify-between">
                                                        <div className="flex justify-between items-center">
                                                            <div className="w-8 h-6 rounded bg-gradient-to-br from-nanobank-amber-deep to-nanobank-orange-deep opacity-80 shadow-inner"></div>
                                                            
                                                            <div className="w-6 h-6 rounded-full bg-white/10 flex items-center justify-center font-bold text-xs border border-white/10">
                                                                N
                                                            </div>
                                                        </div>

                                                        <div>
                                                            <p className="text-sm font-mono tracking-widest text-slate-100">
                                                                {formatCardNumber(card.account_number)}
                                                            </p>
                                                        </div>

                                                        <div className="flex justify-between items-end">
                                                            <div>
                                                                <p className="text-[7px] uppercase tracking-wider text-slate-400">Card Holder</p>
                                                                <p className="text-xs font-semibold tracking-wide text-slate-200 capitalize">
                                                                    {profile.first_name} {profile.last_name}
                                                                </p>
                                                            </div>
                                                            <div className="text-right">
                                                                <p className="text-[7px] uppercase tracking-wider text-slate-400">Status</p>
                                                                <p className="text-[10px] font-semibold text-emerald-400 capitalize">{card.status}</p>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>

                                            {/* Right Column: Metrics & Progress Bar */}
                                            <div className="md:col-span-7 space-y-4">
                                                <div className="flex justify-between text-sm">
                                                    <span className="text-slate-400 font-medium">Used Balance:</span>
                                                    <span className="text-white font-extrabold">{formatCurrency(used)}</span>
                                                </div>

                                                <div className="flex justify-between text-sm">
                                                    <span className="text-slate-400 font-medium">Available Credit:</span>
                                                    <span className="text-emerald-400 font-extrabold">{formatCurrency(available)}</span>
                                                </div>

                                                <div className="flex justify-between text-sm border-b border-white/5 pb-2">
                                                    <span className="text-slate-400 font-medium">Credit Limit:</span>
                                                    <span className="text-slate-200 font-bold">{formatCurrency(limit)}</span>
                                                </div>

                                                {/* Usage Progress Bar */}
                                                <div className="space-y-1">
                                                    <div className="flex justify-between text-[11px] text-slate-500 font-semibold">
                                                        <span>CREDIT UTILIZATION</span>
                                                        <span>{usagePercent.toFixed(1)}%</span>
                                                    </div>
                                                    <div className="w-full h-2 rounded-full bg-white/5 overflow-hidden">
                                                        <div 
                                                            className={`h-full rounded-full transition-all duration-500 ${
                                                                usagePercent > 80 ? 'bg-rose-500' :
                                                                usagePercent > 50 ? 'bg-nanobank-amber-deep' :
                                                                'bg-nanobank-blue-sky'
                                                            }`}
                                                            style={{ width: `${usagePercent}%` }}
                                                        />
                                                    </div>
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
