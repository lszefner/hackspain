import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Albertito · pagos a proveedores",
  description:
    "Trabajador digital para las 500 sombras de Alberto: decide, escala y deja rastro de todo.",
};

/**
 * Sin cabecera ni nav.
 *
 * La interfaz es la mesa (desk/mock), que se sirve estática y no pasa por
 * aquí. Lo único que renderiza este layout es el gate de /access, y llevaba
 * encima la marca y el menú de tito.ai: quien abría el sitio se encontraba de
 * primeras la interfaz que #21 retiró, y el navegador se ponía a precargar
 * /facturas, /muro y /bandeja. Las páginas antiguas siguen accesibles por su
 * URL; lo que ya no hacen es anunciarse en la puerta.
 */
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body className={`${geistSans.variable} ${geistMono.variable} font-sans antialiased`}>
        {children}
      </body>
    </html>
  );
}
