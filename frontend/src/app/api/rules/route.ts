import { NextResponse } from "next/server";
import rules from "@/desk-data/rules.json";

export function GET() {
  return NextResponse.json(rules);
}
