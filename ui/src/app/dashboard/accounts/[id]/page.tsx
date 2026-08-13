import { requireSession } from "@/lib/session";
import { Metadata } from 'next';
import BackLink from "@/components/BackLink";
import GlassCard from "@/components/GlassCard";
import GradientHeading from "@/components/GradientHeading";

export const metadata: Metadata = {
  title: 'Nano-Bank - Account Details',
};

type Props = {
  params: Promise<{ id: string }>;
};

export default async function AccountDetailsPage({ params }: Props) {
    await requireSession();
    const { id } = await params;

    return (
        <main className="relative z-10 flex-1 flex flex-col items-center justify-center px-6 py-12">
            <div className="w-full max-w-3xl">
                <BackLink href="/dashboard/accounts">Back to Accounts</BackLink>

                {/* Details Card */}
                <GlassCard>
                    <div className="mb-8 border-b border-white/10 pb-6">
                        <GradientHeading>Account Details</GradientHeading>
                        <p className="text-slate-400 text-xs mt-1 font-mono">
                            Account ID: {id}
                        </p>
                    </div>

                    <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 p-8 text-center text-sm text-slate-400">
                        Your account details will show up here.
                    </div>
                </GlassCard>
            </div>
        </main>
    );
}
