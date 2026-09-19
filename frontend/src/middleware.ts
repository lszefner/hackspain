import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const GATE_COOKIE = "site-access";
const PUBLIC_PATHS = ["/access", "/api/access"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Retire previously exported invoice PDFs and demo remittances. Original
  // documents now come through the authenticated canonical backend proxy.
  if (pathname.startsWith("/desk/facturas/") || pathname === "/desk/sepa.xml") {
    return new NextResponse("Static demo document retired", { status: 410 });
  }

  const isPublic =
    PUBLIC_PATHS.some((path) => pathname.startsWith(path)) ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon.ico");

  if (isPublic) {
    return NextResponse.next();
  }

  const hasAccess = request.cookies.get(GATE_COOKIE)?.value === "granted";
  if (hasAccess) {
    return NextResponse.next();
  }

  // Un fetch sin cookie seguiría el 302 hasta /access y luego res.json()
  // reventaría con "Unexpected token '<'". En mitad de una llamada eso es
  // indescifrable; un 401 con cuerpo JSON se lee de un vistazo.
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "sin_acceso" }, { status: 401 });
  }

  const url = request.nextUrl.clone();
  url.pathname = "/access";
  url.search = "";
  url.searchParams.set("next", pathname);
  return NextResponse.redirect(url);
}

export const config = {
  // El audio de la centralita queda fuera: son una petición por frase, y no
  // hay nada que decidir sobre ellas. Además, si la cookie caduca a mitad de
  // llamada, el <audio> recibiría HTML en vez de un mp3 y la voz degradaría
  // a la del navegador sin que nadie se entere de por qué.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|phone_calls/voz/).*)"],
};
