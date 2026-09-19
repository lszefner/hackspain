import type { NextRequest } from "next/server";
import { createHash } from "node:crypto";
import archive from "@/desk-data/archive.json";
import facts from "@/desk-data/facts.json";
import system from "@/desk-data/system.json";
import { batchBlock } from "@/lib/desk/batch";

export const runtime = "nodejs";
export const maxDuration = 60;

const KNOWN = new Set((archive as { file: string }[]).map((r) => r.file));
const { system: SYSTEM, model: MODEL, base_url: BASE_URL } = system as {
  system: string; model: string; base_url: string;
};
const UA = "curl/8.7.1";

type File = { name: string; size?: number; ok?: boolean; reason?: string; invoices?: string[] };

/** serve.py: seeded(). sha256 of the name, first 8 hex digits. */
function seeded(name: string, lo: number, hi: number) {
  const h = parseInt(createHash("sha256").update(name, "utf8").digest("hex").slice(0, 8), 16);
  return lo + (h % (hi - lo + 1));
}
const human = (n: number) =>
  n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.floor(n / 1024))} KB`;
const clock = (s: number) =>
  s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`;

/** serve.py: outcome(). Fold the inspected files into one batch result. */
function outcome(files: File[]) {
  const invoices: string[] = [];
  const rejected: [string, string][] = [];
  let size = 0;
  for (const f of files) {
    size += f.size ?? 0;
    if (!f.ok) { rejected.push([f.name, f.reason ?? "could not read it"]); continue; }
    invoices.push(...(f.invoices ?? []));
  }
  const dupes = invoices.filter((i) => KNOWN.has(i));
  const fresh = invoices.filter((i) => !KNOWN.has(i));

  let pay = 0, esc = 0, no = 0, payEur = 0, escEur = 0, noEur = 0;
  for (const i of fresh) {
    // skewed low, like the real population: many small, a few large
    const base = seeded(i, 1, 1000);
    const amount = Math.round((70 + Math.pow(base / 1000, 2.4) * 21000) * 100) / 100;
    const bucket = seeded(i + "v", 1, 100);
    if (bucket <= 77) { pay += 1; payEur += amount; }
    else if (bucket <= 92) { esc += 1; escEur += amount; }
    else { no += 1; noEur += amount; }
  }
  const r2 = (n: number) => Math.round(n * 100) / 100;
  return {
    label: files.length === 1 ? files[0].name : `${files.length} files`,
    total: invoices.length, size: human(size),
    seconds: clock(Math.max(1, Math.round(fresh.length * 4.8))),
    pay, pay_eur: r2(payEur),
    escalate: esc, escalate_eur: r2(escEur),
    patterns: esc ? Math.min(esc, 2) : 0,
    nopay: no, nopay_eur: r2(noEur),
    dupes, rejected,
  };
}

export async function POST(request: NextRequest) {
  let body: { files?: File[]; text?: string; history?: unknown[] };
  try { body = await request.json(); } catch { return new Response("bad request", { status: 400 }); }
  const files = body.files ?? [];
  if (!files.length) return new Response("bad request", { status: 400 });

  const said = String(body.text ?? "").trim().slice(0, 1500);
  const b = outcome(files);
  const panel = batchBlock(b);
  const key = process.env.HELMCODE_API_KEY || process.env.DEEPSEEK_API_KEY;

  const enc = new TextEncoder();
  const send = (c: ReadableStreamDefaultController, o: unknown) =>
    c.enqueue(enc.encode(`data: ${JSON.stringify(o)}\n\n`));

  const stream = new ReadableStream({
    async start(c) {
      const t0 = Date.now();
      send(c, { type: "blocks", blocks: [panel] });

      const messages = [
        { role: "system", content: SYSTEM },
        {
          role: "system",
          content:
            `FACTS (the only source of truth):\n${JSON.stringify({ ...facts, the_batch_just_dropped: b })}\n\n` +
            `ON SCREEN under your sentence: the result of the batch Alberto just dropped ` +
            `(counts, amounts, duplicates, files you could not read). Do not restate it.\n` +
            `The verdicts in this batch are demo data and the panel says so; do not claim ` +
            `you truly read the PDFs.` +
            (said ? `\nAlberto sent the batch with a message; answer THAT, using the batch result.` : ""),
        },
        {
          role: "user",
          content: said || `I just sent you ${b.label}. In one or two sentences, what would you do first with it?`,
        },
      ];

      if (!key) {
        send(c, { type: "delta", text: "The model has no key on this deployment, so there is no sentence. What is below is the batch as the desk folded it." });
        send(c, { type: "done", model: MODEL, ms: Date.now() - t0, error: true });
        return c.close();
      }
      try {
        const r = await fetch(`${BASE_URL}/chat/completions`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${key}`, "Content-Type": "application/json",
            Accept: "text/event-stream", "User-Agent": UA,
          },
          body: JSON.stringify({
            model: MODEL, messages, stream: true, max_tokens: 300,
            temperature: 0.3, stream_options: { include_usage: true },
          }),
        });
        if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let raw = "", got = false;
        let usage: { total_tokens?: number } | null = null;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          raw += dec.decode(value, { stream: true });
          const parts = raw.split("\n");
          raw = parts.pop() ?? "";
          for (const p of parts) {
            const line = p.trim();
            if (!line.startsWith("data:")) continue;
            const payload = line.slice(5).trim();
            if (payload === "[DONE]") continue;
            let ev;
            try { ev = JSON.parse(payload); } catch { continue; }
            if (ev.usage) usage = ev.usage;
            for (const ch of ev.choices ?? []) {
              const piece = ch.delta?.content;
              if (piece) { got = true; send(c, { type: "delta", text: piece }); }
            }
          }
        }
        if (!got) send(c, { type: "delta", text: "The model came back empty. What is below is still real." });
        send(c, { type: "done", model: MODEL, ms: Date.now() - t0, tokens: usage?.total_tokens });
      } catch (err) {
        const msg = String(err instanceof Error ? err.message : err)
          .replace(/sk-[A-Za-z0-9_-]+/g, "[key]").slice(0, 200);
        send(c, { type: "delta", text: `I could not reach the model (${msg}). What is below comes straight from the desk, so it is still right.` });
        send(c, { type: "done", model: MODEL, ms: Date.now() - t0, error: true });
      }
      c.close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store, no-transform",
      Connection: "keep-alive",
    },
  });
}
