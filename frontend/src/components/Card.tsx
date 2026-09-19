export function Card({
  titulo, children, className = "",
}: { titulo?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-line bg-card p-5 ${className}`}>
      {titulo && (
        <h2 className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
          {titulo}
        </h2>
      )}
      {children}
    </section>
  );
}
