export default function GlassCard({ className = "", children }: { className?: string; children: React.ReactNode }) {
    return (
        <div className={`w-full bg-gradient-to-br from-white/10 to-white/5 border border-white/15 backdrop-blur-xl rounded-2xl p-8 shadow-[0_20px_50px_rgba(0,0,0,0.5)] ${className}`}>
            {children}
        </div>
    );
}
