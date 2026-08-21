import { requireSession } from "@/lib/session";
import { Metadata } from 'next';
import { API_BASE_URL } from "@/lib/config";
import { Loan } from "@/lib/loans";
import { Account } from "@/lib/accounts";
import { AlertCircle } from "lucide-react";
import BackLink from "@/components/BackLink";
import GlassCard from "@/components/GlassCard";
import GradientHeading from "@/components/GradientHeading";
import LoanDetailsClient from "./LoanDetailsClient";

export const metadata: Metadata = {
  title: 'Nano-Bank - Loan Details',
};

interface LoanDetailPageProps {
  params: Promise<{ id: string }>;
}

export default async function LoanDetailPage({ params }: LoanDetailPageProps) {
    const { id } = await params;
    const { accessToken } = await requireSession();

    let loan: Loan | null = null;
    let depositAccounts: Account[] = [];
    let loanAccount: Account | null = null;
    let fetchError = false;

    try {
        // 1. Fetch loan details
        const loanResponse = await fetch(`${API_BASE_URL}/api/v1/loans/${id}`, {
            headers: { Authorization: `Bearer ${accessToken}` },
            cache: "no-store",
        });

        if (loanResponse.ok) {
            loan = await loanResponse.json();

            // 2. Fetch loan account and all customer accounts in parallel
            if (loan) {
                const [accResponse, loanAccResponse] = await Promise.all([
                    fetch(`${API_BASE_URL}/api/v1/accounts`, {
                        headers: { Authorization: `Bearer ${accessToken}` },
                        cache: "no-store",
                    }),
                    fetch(`${API_BASE_URL}/api/v1/accounts/${loan.account_id}`, {
                        headers: { Authorization: `Bearer ${accessToken}` },
                        cache: "no-store",
                    })
                ]);

                if (accResponse.ok) {
                    const allAccounts: Account[] = await accResponse.json();
                    depositAccounts = allAccounts.filter(
                        (a) => (a.account_type === "chequing" || a.account_type === "savings") && a.status === "active"
                    );
                } else {
                    console.error("Failed to fetch deposit accounts");
                    fetchError = true;
                }

                if (loanAccResponse.ok) {
                    loanAccount = await loanAccResponse.json();
                } else {
                    console.error("Failed to fetch backing loan account details");
                }
            }
        } else {
            console.error(`Failed to fetch loan: ${loanResponse.status}`);
            fetchError = true;
        }
    } catch (error) {
        console.error("Failed to fetch loan details:", error);
        fetchError = true;
    }

    const formatCurrency = (val: number | string) => {
        const num = typeof val === "string" ? parseFloat(val) : val;
        return new Intl.NumberFormat("en-CA", {
            style: "currency",
            currency: "CAD",
        }).format(num);
    };

    return (
        <main className="relative z-10 flex-1 flex flex-col items-center justify-center px-6 py-12">
            <div className="w-full max-w-3xl">
                <BackLink href="/dashboard/loans">Back to Loans</BackLink>

                <GlassCard>
                    {fetchError || !loan ? (
                        <div className="flex items-center gap-3 p-4 rounded-xl border border-rose-500/20 bg-rose-500/10 text-rose-300 text-sm">
                            <AlertCircle className="w-5 h-5 flex-shrink-0" />
                            <div>
                                <span className="font-semibold">Unable to load loan details.</span> Make sure the API server is running and the loan exists.
                            </div>
                        </div>
                    ) : (
                        <div className="space-y-6">
                            {/* Title & Header */}
                            <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-white/5 pb-4 gap-2">
                                <div>
                                    <GradientHeading>Loan Account Hub</GradientHeading>
                                    <p className="text-slate-500 text-xs font-semibold mt-1 uppercase tracking-wider font-mono">
                                        ID: {loan.loan_id}
                                    </p>
                                </div>
                                <div className="text-left sm:text-right">
                                    <p className="text-slate-500 text-[10px] font-semibold uppercase tracking-wider">Approved Principal</p>
                                    <p className="text-xl font-black text-white">{formatCurrency(loan.principal_amount)}</p>
                                </div>
                            </div>

                            {/* Client Actions and Metrics Component */}
                            <LoanDetailsClient 
                                loan={loan} 
                                depositAccounts={depositAccounts} 
                                loanAccount={loanAccount} 
                            />
                        </div>
                    )}
                </GlassCard>
            </div>
        </main>
    );
}
