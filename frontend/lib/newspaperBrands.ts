/** Known newspaper slugs → favicon domain + calm accent for fallback initials. */
export type NewspaperBrandVisual = {
  domain: string;
  accent: string;
  short: string;
};

const KNOWN: Record<string, NewspaperBrandVisual> = {
  "the-hindu": { domain: "thehindu.com", accent: "terracotta", short: "TH" },
  mint: { domain: "livemint.com", accent: "sage", short: "Mi" },
  "business-standard": { domain: "business-standard.com", accent: "lavender", short: "BS" },
  "hindustan-times": { domain: "hindustantimes.com", accent: "forest", short: "HT" },
  "financial-express": { domain: "financialexpress.com", accent: "lavender", short: "FE" },
  "the-economic-times": { domain: "economictimes.com", accent: "terracotta", short: "ET" },
  "new-indian-express": { domain: "newindianexpress.com", accent: "forest", short: "NIE" },
  "deccan-chronicle": { domain: "deccanchronicle.com", accent: "lavender", short: "DC" },
  "new-hans": { domain: "newhans.com", accent: "gray", short: "NH" },
};

function titleShort(title: string): string {
  const words = title.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

export function newspaperBrandVisual(slug: string, title: string): NewspaperBrandVisual {
  const known = KNOWN[slug];
  if (known) return known;
  const guess = slug.replace(/-/g, "");
  return {
    domain: `${guess}.com`,
    accent: "lavender",
    short: titleShort(title),
  };
}

export function newspaperFaviconUrl(domain: string, size = 64): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=${size}`;
}
