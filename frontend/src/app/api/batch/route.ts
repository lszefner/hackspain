// New uploads have no persisted canonical ingest contract on this frontend yet.
// Never synthesize decisions from names or treat same-name files as duplicates.
export async function POST() {
  const events = [
    { type: "delta", text: "These files have not been processed or saved. Upload processing is not connected here yet. Use Invoices to inspect records already processed by the backend." },
    { type: "done", error: true },
  ];
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" } });
}
