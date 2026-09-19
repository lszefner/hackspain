type AccessPageProps = {
  searchParams: Promise<{ next?: string; error?: string }>;
};

export default async function AccessPage({ searchParams }: AccessPageProps) {
  const { next = "/", error } = await searchParams;

  return (
    <main className="flex min-h-screen items-center justify-center bg-zinc-50 px-6">
      <div className="w-full max-w-sm rounded-xl border border-zinc-200 bg-white p-8 shadow-sm">
        <h1 className="text-lg font-semibold tracking-tight text-zinc-900">
          Acceso privado
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
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
            className="rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-400"
          />
          <button
            type="submit"
            className="rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-800"
          >
            Entrar
          </button>
          {error && (
            <p className="text-sm text-red-600">Código incorrecto. Inténtalo de nuevo.</p>
          )}
        </form>
      </div>
    </main>
  );
}
