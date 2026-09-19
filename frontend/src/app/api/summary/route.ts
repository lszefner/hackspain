import { NextResponse } from "next/server";
import summary from "@/desk-data/summary.json";

export function GET() {
  return NextResponse.json(summary);
}
