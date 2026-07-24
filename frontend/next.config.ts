import type { NextConfig } from "next";

const API_PROXY_URL = process.env.API_PROXY_URL ?? "http://127.0.0.1:8200";

const nextConfig: NextConfig = {
  output: "standalone",
  // Strict Mode double-invokes effects in dev, which leaves framer-motion's
  // AnimatePresence panels stuck (blank persona/product tabs, etc.). Prod never
  // runs Strict Mode; turning it off in dev makes the two match and the
  // animations reliable. (Native-IO reveals are already strict-mode safe.)
  reactStrictMode: false,
  // Next 16 blocks cross-origin access to /_next/* (HMR, fonts) by default.
  // Local QA often uses http://127.0.0.1:3000 while the server identity is
  // localhost - without this, client hydration / HMR breaks on 127.0.0.1.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
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
