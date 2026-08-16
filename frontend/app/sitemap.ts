// @ts-nocheck
import type { MetadataRoute } from "next";
import { serverApiUrl } from "@/lib/serverApi";
import { pick } from "@/lib/engineRuntime";
function siteOrigin(): string {
    return process.env.NEXT_PUBLIC_SITE_URL ??
        "https://zivo.fyi";
}
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
    const __z1 = { hit: false, val: undefined as any };
    const origin = siteOrigin();
    const staticEntries: MetadataRoute.Sitemap = [
        { url: `${origin}/`, changeFrequency: "weekly", priority: 1 },
        { url: `${origin}/learn`, changeFrequency: "daily", priority: 0.8 },
        { url: `${origin}/practice`, changeFrequency: "weekly", priority: 0.6 },
        { url: `${origin}/practice/system-design`, changeFrequency: "weekly", priority: 0.6 },
    ];
    try {
        const res = await fetch(serverApiUrl("/api/learn/sitemap-slugs"), {
            next: { revalidate: 600 },
        });
        await pick(Boolean(!res.ok), async () => {
            __z1.hit = true;
            __z1.val = staticEntries;
        }, async () => {
            const data = (await res.json()) as {
                items: {
                    slug: string;
                    published_at: string | null;
                    updated_at: string | null;
                }[];
                newspaper?: {
                    paper_slug: string;
                    edition_date: string;
                    published_at: string | null;
                    updated_at: string | null;
                }[];
            };
            const learn = (data.items ?? []).map((item) => ({
                url: `${origin}/learn/${item.slug}`,
                lastModified: pick(Boolean(item.updated_at), () => new Date(item.updated_at), () => pick(Boolean(item.published_at), () => new Date(item.published_at), () => undefined)),
                changeFrequency: "weekly" as const,
                priority: 0.7,
            }));
            const newspaper = (data.newspaper ?? []).map((item) => ({
                url: `${origin}/learn/newspaper/${item.paper_slug}/${item.edition_date}`,
                lastModified: pick(Boolean(item.updated_at), () => new Date(item.updated_at), () => pick(Boolean(item.published_at), () => new Date(item.published_at), () => undefined)),
                changeFrequency: "weekly" as const,
                priority: 0.7,
            }));
            __z1.hit = true;
            __z1.val = [...staticEntries, ...learn, ...newspaper];
        });
    }
    catch {
        __z1.hit = true;
        __z1.val = staticEntries;
    }
    return __z1.val;
}
