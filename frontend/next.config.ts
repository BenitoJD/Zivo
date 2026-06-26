import type { NextConfig } from "next";

const API_PROXY_URL = process.env.API_PROXY_URL ?? "http://127.0.0.1:8200";

const nextConfig: NextConfig = {
  output: "standalone",
  // Strict Mode double-invokes effects in dev, which leaves framer-motion's
  // AnimatePresence panels stuck (blank persona/product tabs, etc.). Prod never
  // runs Strict Mode; turning it off in dev makes the two match and the
  // animations reliable. (Native-IO reveals are already strict-mode safe.)
  reactStrictMode: false,
  experimental: {
    optimizePackageImports: ["@mantine/core", "@mantine/hooks"],
    // Dev rewrites proxy /api/* to FastAPI; default 10MB truncates uploads.
    proxyClientMaxBodySize: "1024mb",
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_PROXY_URL}/api/:path*` },
      { source: "/health", destination: `${API_PROXY_URL}/health` },
    ];
  },
};

export default nextConfig;
