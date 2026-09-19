import type { NextRequest } from "next/server";
import { proxyDesk } from "@/lib/desk/backend";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) {
  return proxyDesk("rules", request.nextUrl.searchParams);
}
