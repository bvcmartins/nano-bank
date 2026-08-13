import Link from "next/link";
import { ArrowLeft } from "lucide-react";

export default function BackLink({ href, children }: { href: string; children: React.ReactNode }) {
    return (
        <Link href={href} className="inline-flex items-center gap-2 text-slate-400 hover:text-white transition-colors text-sm mb-6 group">
            <ArrowLeft className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" />
            {children}
        </Link>
    );
}
