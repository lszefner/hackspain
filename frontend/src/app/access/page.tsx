type AccessPageProps = {
  searchParams: Promise<{ next?: string; error?: string }>;
};

export default async function AccessPage({ searchParams }: AccessPageProps) {
  const { next = "/", error } = await searchParams;

  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-6">
      <div className="w-full max-w-sm rounded-xl border border-line bg-card p-8 shadow-sm">
        <h1 className="text-lg font-bold tracking-tight text-ink">Acceso privado</h1>
        <p className="mt-1 text-sm text-muted">
          Este demo es privado. Introduce el código de acceso para continuar.
        </p>

        <form action="/api/access" method="POST" className="mt-6 flex flex-col gap-3">
          <input type="hidden" name="next" value={next} />
          <input
            type="password"
            name="code"
            placeholder="Código de acceso"
            autoFocus
            required
            className="rounded-md border border-line bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-accent"
          />
          <button
            type="submit"
            className="rounded-md bg-accent px-3 py-2 text-sm font-medium text-white transition-colors hover:opacity-90"
          >
            Entrar
          </button>
          {error && (
            <p className="text-sm text-bad">Código incorrecto. Inténtalo de nuevo.</p>
          )}
        </form>
      </div>
    </main>
  );
}
