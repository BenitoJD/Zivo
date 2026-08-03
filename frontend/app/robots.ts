import type { MetadataRoute } from "next";

function siteOrigin(): string {
  return process.env.NEXT_PUBLIC_SITE_URL ?? "https://zivo.fyi";
}

export default function robots(): MetadataRoute.Robots {
  const origin = siteOrigin();
  return {
    rules: {
      userAgent: "*",
      // Disallow-only: a restrictive Allow list blocked /favicon.ico from Googlebot.
      disallow: ["/workspace", "/api/", "/login", "/signup"],
    },
    sitemap: `${origin}/sitemap.xml`,
  };
}
