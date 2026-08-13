import { requireSession } from "@/lib/session";
import { Metadata } from 'next';
import { PiggyBank, Wallet, AlertCircle, Plus } from "lucide-react";
import { API_BASE_URL } from "@/lib/config";
import { Account } from "@/lib/accounts";
import BackLink from "@/components/BackLink";
import GlassCard from "@/components/GlassCard";
import GradientHeading from "@/components/GradientHeading";
import Link from "next/link";

export const metadata: Metadata = {
  title: 'Nano-Bank - Cash & Savings',
};

export default async function AccountsPage() {
    const { accessToken } = await requireSession();

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

    // Filter only chequing and savings
    const depositAccounts = accounts.filter(
        (a) => a.account_type === "chequing" || a.account_type === "savings"
    );

    const totalDepositMoney = depositAccounts.reduce(
        (sum, a) => sum + parseFloat(a.balance || "0"),
        0
    );

    const formatCurrency = (val: number) => {
        return new Intl.NumberFormat("en-CA", {
            style: "currency",
            currency: "CAD",
        }).format(val);
    };

    const formatAccountNumber = (num: string) => {
        // Formats as 4-4-4: "1234 5678 9012"
        return num.replace(/(\d{4})(?=\d)/g, "$1 ");
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
                                <GradientHeading>Cash & Savings</GradientHeading>
                                <Link
                                    href="/dashboard/accounts/create"
                                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-nanobank-blue-sky/30 bg-nanobank-blue-sky/10 text-nanobank-blue-sky hover:bg-nanobank-blue-sky/20 transition-all text-xs font-semibold self-start sm:self-center cursor-pointer"
                                >
                                    <Plus className="w-3.5 h-3.5" />
                                    Open Account
                                </Link>
                            </div>
                        </div>
                        <div className="text-right">
                            <h2 className="text-3xl font-black text-emerald-400 mt-1">
                                {formatCurrency(totalDepositMoney)}
                            </h2>
                        </div>
                    </div>

                    {fetchError ? (
                        <div className="flex items-center gap-3 p-4 rounded-xl border border-rose-500/20 bg-rose-500/10 text-rose-300 text-sm">
                            <AlertCircle className="w-5 h-5 flex-shrink-0" />
                            <div>
                                <span className="font-semibold">Error fetching account details</span>
                            </div>
                        </div>
                    ) : depositAccounts.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 p-8 text-center text-sm text-slate-400">
                            You do not have any active deposit accounts yet.
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {depositAccounts.map((account) => {
                                const isChequing = account.account_type === "chequing";
                                return (
                                    <Link 
                                        key={account.account_id}
                                        href={`/dashboard/accounts/${account.account_id}`}
                                        className="flex flex-col sm:flex-row sm:items-center justify-between p-6 rounded-xl border border-white/5 bg-slate-900/40 hover:border-white/15 hover:bg-slate-900/60 transition-all duration-300 cursor-pointer hover:scale-[1.01] transform block group"
                                    >
                                        <div className="flex items-center gap-4 mb-4 sm:mb-0">
                                            <div className={`p-3 rounded-lg ${isChequing ? 'bg-nanobank-blue-sky/10 text-nanobank-blue-sky group-hover:scale-105' : 'bg-emerald-500/10 text-emerald-400 group-hover:scale-105'} transition-transform`}>
                                                {isChequing ? <Wallet className="w-6 h-6" /> : <PiggyBank className="w-6 h-6" />}
                                            </div>
                                            <div>
                                                <div className="flex items-center gap-2">
                                                    <h3 className="text-lg font-bold text-white capitalize group-hover:underline">
                                                        {account.account_type} Account
                                                    </h3>
                                                    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
                                                        account.status === "active" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" :
                                                        account.status === "frozen" ? "bg-nanobank-blue-sky/10 text-nanobank-blue-sky border border-nanobank-blue-sky/20" :
                                                        "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                                                    }`}>
                                                        {account.status}
                                                    </span>
                                                </div>
                                                <p className="text-xs text-slate-400 mt-1 font-mono">
                                                    Account No. {formatAccountNumber(account.account_number)}
                                                </p>
                                            </div>
                                        </div>
                                        <div className="text-left sm:text-right">
                                            <span className="text-slate-500 text-[10px] font-semibold uppercase tracking-wider block sm:inline">
                                                Available Balance
                                            </span>
                                            <p className="text-2xl font-extrabold text-white mt-0.5">
                                                {formatCurrency(parseFloat(account.balance))}
                                            </p>
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
