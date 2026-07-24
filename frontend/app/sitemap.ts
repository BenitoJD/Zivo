import type { MetadataRoute } from "next";

function apiBase(): string {
  return process.env.API_PROXY_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8200";
}

function siteOrigin(): string {
  return process.env.NEXT_PUBLIC_SITE_URL ?? "https://zivo.fyi";
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const origin = siteOrigin();
  const staticEntries: MetadataRoute.Sitemap = [
    { url: `${origin}/`, changeFrequency: "weekly", priority: 1 },
    { url: `${origin}/learn`, changeFrequency: "daily", priority: 0.8 },
    { url: `${origin}/practice`, changeFrequency: "weekly", priority: 0.6 },
    { url: `${origin}/practice/system-design`, changeFrequency: "weekly", priority: 0.6 },
  ];

  try {
    const res = await fetch(`${apiBase()}/api/learn/sitemap-slugs`, {
      next: { revalidate: 600 },
    });
    if (!res.ok) return staticEntries;
    const data = (await res.json()) as {
      items: { slug: string; published_at: string | null; updated_at: string | null }[];
    };
    const learn = (data.items ?? []).map((item) => ({
      url: `${origin}/learn/${item.slug}`,
      lastModified: item.updated_at
        ? new Date(item.updated_at)
        : item.published_at
          ? new Date(item.published_at)
          : undefined,
      changeFrequency: "weekly" as const,
      priority: 0.7,
    }));
    return [...staticEntries, ...learn];
  } catch {
    return staticEntries;
  }
}
