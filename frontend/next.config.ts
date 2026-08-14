import type { NextConfig } from "next";

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
    const authProxy = process.env.AUTH_PROXY_URL ?? "http://127.0.0.1:8201";
    const storageProxy = process.env.STORAGE_PROXY_URL ?? "http://127.0.0.1:8202";
    const practiceProxy = process.env.PRACTICE_PROXY_URL ?? "http://127.0.0.1:8203";
    const contentProxy = process.env.CONTENT_PROXY_URL ?? "http://127.0.0.1:8204";
    const studyProxy = process.env.STUDY_PROXY_URL ?? "http://127.0.0.1:8205";
    const libraryProxy = process.env.LIBRARY_PROXY_URL ?? "http://127.0.0.1:8206";
    const adminProxy = process.env.ADMIN_PROXY_URL ?? "http://127.0.0.1:8207";
    return [
      { source: "/api/auth/:path*", destination: `${authProxy}/api/auth/:path*` },
      { source: "/api/storage/:path*", destination: `${storageProxy}/api/storage/:path*` },
      { source: "/api/coding/:path*", destination: `${practiceProxy}/api/coding/:path*` },
      { source: "/api/coding", destination: `${practiceProxy}/api/coding` },
      { source: "/api/system-design/:path*", destination: `${practiceProxy}/api/system-design/:path*` },
      { source: "/api/system-design", destination: `${practiceProxy}/api/system-design` },
      { source: "/api/practice/:path*", destination: `${practiceProxy}/api/practice/:path*` },
      { source: "/api/newspaper/admin/:path*", destination: `${contentProxy}/api/newspaper/admin/:path*` },
      { source: "/api/newspaper/:path*", destination: `${practiceProxy}/api/newspaper/:path*` },
      { source: "/api/learn/:path*", destination: `${contentProxy}/api/learn/:path*` },
      { source: "/api/learn", destination: `${contentProxy}/api/learn` },
      { source: "/api/artifacts/:path*", destination: `${studyProxy}/api/artifacts/:path*` },
      { source: "/api/assertions/:path*", destination: `${studyProxy}/api/assertions/:path*` },
      { source: "/api/chat/:path*", destination: `${studyProxy}/api/chat/:path*` },
      { source: "/api/chat", destination: `${studyProxy}/api/chat` },
      { source: "/api/mcq/:path*", destination: `${studyProxy}/api/mcq/:path*` },
      { source: "/api/progress/:path*", destination: `${studyProxy}/api/progress/:path*` },
      { source: "/api/progress", destination: `${studyProxy}/api/progress` },
      { source: "/api/guest/:path*", destination: `${studyProxy}/api/guest/:path*` },
      { source: "/api/guest", destination: `${studyProxy}/api/guest` },
      { source: "/api/offline/:path*", destination: `${studyProxy}/api/offline/:path*` },
      { source: "/api/reference/:path*", destination: `${studyProxy}/api/reference/:path*` },
      { source: "/api/sources/:path*", destination: `${libraryProxy}/api/sources/:path*` },
      { source: "/api/sources", destination: `${libraryProxy}/api/sources` },
      { source: "/api/documents/:path*", destination: `${libraryProxy}/api/documents/:path*` },
      { source: "/api/documents", destination: `${libraryProxy}/api/documents` },
      { source: "/api/activities/:path*", destination: `${libraryProxy}/api/activities/:path*` },
      { source: "/api/audiobook/:path*", destination: `${libraryProxy}/api/audiobook/:path*` },
      { source: "/api/models/:path*", destination: `${adminProxy}/api/models/:path*` },
      { source: "/api/models", destination: `${adminProxy}/api/models` },
      { source: "/api/debug/:path*", destination: `${adminProxy}/api/debug/:path*` },
      { source: "/api/debug", destination: `${adminProxy}/api/debug` },
      { source: "/api/health/:path*", destination: `${authProxy}/api/health/:path*` },
      { source: "/api/health", destination: `${authProxy}/api/health` },
      { source: "/health", destination: `${authProxy}/health` },
    ];
  },
};

export default nextConfig;
