import type { NextRequest } from "next/server";
import { proxyDesk } from "@/lib/desk/backend";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) {
  return proxyDesk("invoices", request.nextUrl.searchParams);
}
