import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Los server actions traen 1 MB por defecto y una factura escaneada pasa
  // de ahí. El tope real lo pone alberto/web/subida.py (MAX_BYTES).
  experimental: { serverActions: { bodySizeLimit: "20mb" } },

  async rewrites() {
    return {
      // La portada ES la mesa. Va en beforeFiles para ganarle a app/page.tsx,
      // que seguía llevando al listado que #21 dio por retirado. Es rewrite y
      // no redirect para que la URL siga siendo la raíz: lo que se enseña es
      // "el sitio", no "una página dentro del sitio".
      beforeFiles: [{ source: "/", destination: "/desk/index.html" }],
      // La centralita, en cambio, va en afterFiles: no tiene que ganarle a
      // nada. Son rutas que no existen ni como página ni como fichero, así
      // que sólo se consultan cuando el sistema de ficheros ya dijo que no.
      afterFiles: [
        // No es una página de Next: es la misma página que sirve `make
        // centralita` en el 8011, publicada tal cual en public/ por `make
        // web-centralita`. Next sirve public/ pero no resuelve solo una ruta
        // sin extensión, así que hay que nombrarla.
        { source: "/phone_calls", destination: "/phone_calls/index.html" },
        // Su index.html pide los assets en `/estatico/*`, en absoluto. Se
        // respeta en vez de reescribir el HTML: si el HTML desplegado dejara
        // de ser idéntico al local, tendríamos dos centralitas que mantener.
        { source: "/estatico/:fichero", destination: "/phone_calls/estatico/:fichero" },
      ],
      fallback: [],
    };
  },
};

export default nextConfig;
