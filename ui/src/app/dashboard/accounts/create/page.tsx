import { requireSession } from "@/lib/session";
import { Metadata } from 'next';
import BackLink from "@/components/BackLink";
import GlassCard from "@/components/GlassCard";
import GradientHeading from "@/components/GradientHeading";
import CreateAccountForm from "./CreateAccountForm";

export const metadata: Metadata = {
  title: 'Nano-Bank - Open New Account',
};

export default async function CreateAccountPage() {
    await requireSession();

    return (
        <main className="relative z-10 flex-1 flex flex-col items-center justify-center px-6 py-12">
            <div className="w-full max-w-3xl">
                <BackLink href="/dashboard/accounts">Back to Accounts</BackLink>

                {/* Content Card */}
                <GlassCard>
                    <div className="mb-8 border-b border-white/10 pb-6">
                        <GradientHeading>Open a New Account</GradientHeading>
                    </div>

                    <CreateAccountForm />
                </GlassCard>
            </div>
        </main>
    );
}
