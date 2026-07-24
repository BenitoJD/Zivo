/** Clamp paragraph-length aspect labels so report/progress chips stay readable. */
export function shortTopicName(raw: string, maxChars = 56): string {
  let s = raw.replace(/\s+/g, " ").trim() || "General";
  for (const sep of [". ", "? ", "! ", "; ", " — ", " – ", " - "]) {
    if (s.includes(sep)) {
      s = s.split(sep)[0]!.trim();
      break;
    }
  }
  // Mid-word residue from newspaper/OCR extracts ("ngress government…").
  if (s && s[0] === s[0].toLowerCase() && s[0] !== s[0].toUpperCase()) {
    const parts = s.split(" ").filter(Boolean);
    const start = parts.findIndex((w) => w[0] === w[0]?.toUpperCase());
    if (start < 0) return "General";
    s = parts.slice(start).join(" ");
  }
  let words = s.split(" ").filter(Boolean);
  if (words.length > 8) words = words.slice(0, 8);
  const dangling = new Set([
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "in",
    "on",
    "to",
    "for",
    "with",
    "from",
    "into",
    "onto",
    "across",
    "by",
    "via",
    "as",
    "at",
    "that",
    "which",
    "who",
    "whom",
    "whose",
    "where",
    "when",
    "is",
    "are",
    "was",
    "were",
  ]);
  while (words.length && dangling.has(words[words.length - 1]!.toLowerCase())) {
    words.pop();
  }
  s = words.join(" ");
  if (s.length > maxChars) {
    s = `${s.slice(0, Math.max(1, maxChars - 1)).replace(/[,;:\-\s]+$/u, "")}…`;
  }
  return s || "General";
}
