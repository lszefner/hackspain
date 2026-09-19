import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/** The remittance is frozen with the rest of the export; hand it to the CDN. */
export function GET(request: NextRequest) {
  return NextResponse.redirect(new URL("/desk/sepa.xml", request.nextUrl.origin));
}
