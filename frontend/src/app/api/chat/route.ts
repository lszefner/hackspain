import type { NextRequest } from "next/server";
import blocks from "@/desk-data/blocks.json";
import invoiceBlocks from "@/desk-data/invoice_blocks.json";
import facts from "@/desk-data/facts.json";
import intents from "@/desk-data/intents.json";
import system from "@/desk-data/system.json";
import { fold } from "@/lib/desk/fold";

export const runtime = "nodejs";
export const maxDuration = 60;

const INTENTS = intents as [string, string[]][];
const BLOCKS = blocks as Record<string, unknown>;
const INVOICE_BLOCKS = invoiceBlocks as Record<string, unknown>;
const FILE_RE = /([\w.\-\u00c0-\u024f]+\.pdf)/i;

/** serve.py: named_file(). A file named in the question wins over the router. */
function namedFile(text: string): string | null {
  const m = FILE_RE.exec(text || "");
  if (!m) return null;
  const name = m[1].split("/").pop() ?? "";
  return name in INVOICE_BLOCKS ? name : null;
}
const { system: SYSTEM, model: MODEL, base_url: BASE_URL } = system as {
  system: string; model: string; base_url: string;
};
// Cloudflare in front of the gateway answers 1010 to the default agent
const UA = "curl/8.7.1";

/**
 * serve.py: route(text). Highest keyword count wins; ties go to the earlier,
 * more specific intent. The model never picks what goes on screen.
 */
function route(text: string): string[] {
  const t = fold(text);
  let best = { score: 0, name: "" };
  // strictly greater, so a tie keeps the earlier -- the more specific -- intent,
  // which is what max() on (score, -i) does in serve.py
  for (const [name, keys] of INTENTS) {
    const score = keys.reduce((n, k) => n + (t.includes(k) ? 1 : 0), 0);
    if (score > best.score) best = { score, name };
  }
  return best.score ? [best.name] : [];
}

function buildMessages(text: string, names: string[], history: unknown[]) {
  const onScreen = names.length ? names.join(", ") : "nothing";
  const ctx =
    `FACTS (the only source of truth):\n${JSON.stringify(facts)}\n\n` +
    `ON SCREEN right now, directly under your sentence: ${onScreen}.\n` +
    `Do not repeat what that list already shows.`;
  const msgs: { role: string; content: string }[] = [
    { role: "system", content: SYSTEM },
    { role: "system", content: ctx },
  ];
  for (const h of (history as { role?: string; content?: string }[]).slice(-6)) {
    if ((h.role === "user" || h.role === "assistant") && h.content) {
      msgs.push({ role: h.role, content: String(h.content).slice(0, 1500) });
    }
  }
  msgs.push({ role: "user", content: text.slice(0, 1500) });
  return msgs;
}

export async function POST(request: NextRequest) {
  let body: { text?: string; history?: unknown[] };
  try {
    body = await request.json();
  } catch {
    return new Response("bad request", { status: 400 });
  }
  const text = String(body.text ?? "").trim();
  if (!text) return new Response("bad request", { status: 400 });

  // a file named in the question wins: he is asking about that invoice
  const named = namedFile(text);
  const names = named ? ["invoice"] : route(text);
  const rendered = named
    ? [INVOICE_BLOCKS[named]]
    : names.map((n) => BLOCKS[n]).filter(Boolean);
  const key = process.env.HELMCODE_API_KEY || process.env.DEEPSEEK_API_KEY;

  const enc = new TextEncoder();
  const send = (c: ReadableStreamDefaultController, o: unknown) =>
    c.enqueue(enc.encode(`data: ${JSON.stringify(o)}\n\n`));

  const stream = new ReadableStream({
    async start(c) {
      const t0 = Date.now();
      send(c, { type: "blocks", blocks: rendered });

      if (!key) {
        // The list below is real either way; only the sentence is missing.
        send(c, { type: "delta", text: "The model has no key on this deployment, so there is no sentence. What is below comes straight from the desk, so it is still right." });
        send(c, { type: "done", model: MODEL, ms: Date.now() - t0, error: true });
        return c.close();
      }

      try {
        const r = await fetch(`${BASE_URL}/chat/completions`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${key}`,
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            "User-Agent": UA,
          },
          body: JSON.stringify({
            model: MODEL,
            messages: buildMessages(text, names, body.history ?? []),
            stream: true,
            max_tokens: 300,
            temperature: 0.3,
            stream_options: { include_usage: true },
          }),
        });
        if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);

        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let raw = "";
        let usage: { total_tokens?: number } | null = null;
        let got = false;

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
          .replace(/sk-[A-Za-z0-9_-]+/g, "[key]")
          .slice(0, 200);
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
