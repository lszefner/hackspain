import { NextResponse } from "next/server";
export async function POST() {
  return NextResponse.json({ said: "No action was taken. This interface reads recorded invoices; human decisions, payments and supplier messages are not connected.", undo: false, refresh: false }, { status: 409 });
}
