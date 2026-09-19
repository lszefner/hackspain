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
      afterFiles: [],
      fallback: [],
    };
  },
};

export default nextConfig;
