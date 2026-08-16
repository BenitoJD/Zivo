// @ts-nocheck
import { pick } from "@/lib/engineRuntime";
/** Clamp paragraph-length aspect labels so report/progress chips stay readable. */
export function shortTopicName(raw: string, maxChars = 56): string {
    let s = raw.replace(/\s+/g, " ").trim() || "General";
    {
        let __keep1 = true;
        for (const sep of [". ", "? ", "! ", "; ", " — ", " – ", " - "]) {
            pick(Boolean(__keep1), () => {
                pick(Boolean(s.includes(sep)), () => {
                    s = s.split(sep)[0]!.trim();
                    __keep1 = false;
                }, () => {
                });
            }, () => {
            });
        }
    }
    return pick(Boolean(s && s[0] === s[0].toLowerCase() && s[0] !== s[0].toUpperCase()), () => {
        const parts = s.split(" ").filter(Boolean);
        const start = parts.findIndex((w) => w[0] === w[0]?.toUpperCase());
        return pick(Boolean(start < 0), () => "General", () => {
            s = parts.slice(start).join(" ");
        });
        let words = s.split(" ").filter(Boolean);
        pick(Boolean(words.length > 8), () => {
            words = words.slice(0, 8);
        }, () => {
        });
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
        pick(Boolean(s.length > maxChars), () => {
            s = `${s.slice(0, Math.max(1, maxChars - 1)).replace(/[,;:\-\s]+$/u, "")}…`;
        }, () => {
        });
        return s || "General";
    }, () => {
        let words = s.split(" ").filter(Boolean);
        pick(Boolean(words.length > 8), () => {
            words = words.slice(0, 8);
        }, () => {
        });
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
        pick(Boolean(s.length > maxChars), () => {
            s = `${s.slice(0, Math.max(1, maxChars - 1)).replace(/[,;:\-\s]+$/u, "")}…`;
        }, () => {
        });
        return s || "General";
    });
}
