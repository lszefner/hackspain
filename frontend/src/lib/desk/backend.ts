import { NextResponse } from "next/server";

export function backendBase() {
  return (process.env.ENGINE_API_BASE ?? process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8010").replace(/\/$/, "");
}
export async function deskQuery(resource: string, params = new URLSearchParams()) {
  const response = await fetch(`${backendBase()}/api/ui/${resource}?${params}`, {
    cache: "no-store", signal: AbortSignal.timeout(20_000),
  });
  if (!response.ok) throw new Error(`Backend query failed (${response.status})`);
  return response.json();
}
export async function proxyDesk(resource: string, params = new URLSearchParams()) {
  try {
    const response = await fetch(`${backendBase()}/api/ui/${resource}?${params}`, {
      cache: "no-store", signal: AbortSignal.timeout(20_000),
    });
    if (resource === "pdf" && response.ok) return new Response(response.body, {
      headers: { "Content-Type": "application/pdf", "Cache-Control": "private, no-store" },
    });
    return NextResponse.json(await response.json(), { status: response.status, headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "Backend unavailable. Check the API connection and retry." }, { status: 503 });
  }
}
