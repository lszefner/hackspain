import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Los server actions traen 1 MB por defecto y una factura escaneada pasa
  // de ahí. El tope real lo pone alberto/web/subida.py (MAX_BYTES).
  experimental: { serverActions: { bodySizeLimit: "20mb" } },
};

export default nextConfig;
