import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(_request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const allowed = (path.length === 1 && ["salud", "resumen", "estado"].includes(path[0]))
    || (path.length === 2 && ["factura", "ejecucion"].includes(path[0]))
    || (path.length === 3 && path[0] === "factura" && path[2] === "flujo");
  if (!allowed || path.some((part) => !part || part === "." || part === ".." || /[/\\]/.test(part))) {
    return NextResponse.json({ error: "invalid_engine_path" }, { status: 400 });
  }
  const base = process.env.ENGINE_API_BASE ?? process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8010";
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/api/${path.map(encodeURIComponent).join("/")}`, {
      cache: "no-store", signal: AbortSignal.timeout(60_000),
    });
    return NextResponse.json(await response.json(), { status: response.status, headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }
}
