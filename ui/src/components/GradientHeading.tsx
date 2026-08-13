export default function GradientHeading({ children }: { children: React.ReactNode }) {
    return (
        <h1 className="text-3xl font-extrabold tracking-tight bg-gradient-to-r from-white via-slate-100 to-nanobank-blue-sky bg-clip-text text-transparent">
            {children}
        </h1>
    );
}
