import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Los server actions traen 1 MB por defecto y una factura escaneada pasa
  // de ahí. El tope real lo pone alberto/web/subida.py (MAX_BYTES).
  experimental: { serverActions: { bodySizeLimit: "20mb" } },

  async redirects() {
    return [
      { source: "/desk", destination: "/?view=invoices", permanent: false },
      {
        source: "/desk/index.html",
        destination: "/?view=invoices",
        permanent: false,
      },
    ];
  },

  async rewrites() {
    return {
      // next dev does not execute Vercel's Python functions.
      beforeFiles: process.env.NODE_ENV === "development" ? [
        { source: "/api/centralita/turno", destination: "http://127.0.0.1:8011/api/turno" },
        { source: "/api/voz/:fichero", destination: "http://127.0.0.1:8011/api/voz/:fichero" },
      ] : [],
      afterFiles: [
        // No es una página de Next: es la misma página que sirve `make
        // centralita` en el 8011, publicada tal cual en public/ por `make
        // web-centralita`. Next sirve public/ pero no resuelve solo una ruta
        // sin extensión, así que hay que nombrarla.
        { source: "/phone_calls", destination: "/phone_calls/index.html" },
        // Su index.html pide los assets en `/estatico/*`, en absoluto. Se
        // respeta en vez de reescribir el HTML: si el HTML desplegado dejara
        // de ser idéntico al local, tendríamos dos centralitas que mantener.
        {
          source: "/estatico/:fichero",
          destination: "/phone_calls/estatico/:fichero",
        },
      ],
      fallback: [],
    };
  },
};

export default nextConfig;
