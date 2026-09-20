import { NextResponse } from "next/server";
import { deskQuery } from "@/lib/desk/backend";
import { classifyOutreach } from "@/lib/desk/outreach";
import { writeEmail } from "@/lib/desk/write-email";

export async function POST(request: Request) {
  let body;
  try { body = await request.json(); } catch {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }
  if (!body || typeof body.file !== "string" || !body.file || /[/\\]/.test(body.file) ||
      (body.recipient !== undefined && typeof body.recipient !== "string")) {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }
  try {
    const detail = await deskQuery("invoice", new URLSearchParams({ file: body.file }));
    const draft = classifyOutreach(detail);
    if (!draft) return NextResponse.json({ error: "no_email_needed" }, { status: 409 });
    return NextResponse.json(writeEmail(draft, body.recipient));
  } catch {
    return NextResponse.json({ error: "Could not load the invoice. No email was written." }, { status: 503 });
  }
}
