import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "tito.ai · pagos a proveedores",
  description:
    "Trabajador digital para las 500 sombras de Alberto: decide, escala y deja rastro de todo.",
};

const NAV = [
  { href: "/facturas", label: "Facturas" },
  { href: "/bandeja", label: "Bandeja" },
  { href: "/logs", label: "Logs" },
  { href: "/finanzas", label: "Finanzas" },
  { href: "/logica", label: "Lógica" },
];

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body className={`${geistSans.variable} ${geistMono.variable} font-sans antialiased`}>
        <header className="sticky top-0 z-10 border-b border-line bg-card/90 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
            <Link href="/" className="flex items-center gap-2.5">
              <svg viewBox="0 0 16 16" className="h-6 w-6 rounded">
                <rect width="16" height="16" fill="#f5f2ec" />
                <rect x="2" y="2" width="4" height="4" fill="#1f6b4a" />
                <rect x="6" y="6" width="4" height="4" fill="#b26a00" />
                <rect x="10" y="10" width="4" height="4" fill="#b0271e" />
              </svg>
              <span className="text-lg font-bold tracking-tight">tito.ai</span>
              <span className="hidden text-xs text-muted sm:inline">
                · técnico de pagos a proveedores
              </span>
            </Link>
            <nav className="ml-auto flex gap-1 text-sm">
              {NAV.map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  className="rounded-md px-3 py-1.5 font-medium text-muted transition-colors hover:bg-soft hover:text-ink"
                >
                  {n.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
        <footer className="mx-auto max-w-6xl px-6 pb-10 text-xs text-muted">
          HackSpain 2026 · track Maisa · todo dato aquí es sintético — y todo lo que
          Albertito decide deja rastro.
        </footer>
      </body>
    </html>
  );
}
