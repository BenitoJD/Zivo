// @ts-nocheck
import { pick } from "@/lib/engineRuntime";
/** Parse ```` ```zv-mcq ```` JSON blocks out of an assistant chat reply. */
export type ChatMcq = {
    question: string;
    options: string[];
    correct_index: number;
    explanation?: string;
};
const ZV_MCQ_RE = /```zv-mcq\s*(\{[\s\S]*?\})\s*```/g;
export function parseChatMcqs(content: string): ChatMcq[] {
    const out: ChatMcq[] = [];
    return pick(Boolean(!content), () => out, () => {
        for (const match of content.matchAll(ZV_MCQ_RE)) {
            try {
                const raw = JSON.parse(match[1]) as Partial<ChatMcq>;
                const question = String(raw.question ?? "").trim();
                const options = pick(Boolean(Array.isArray(raw.options)), () => raw.options.map((o) => String(o).trim()).filter(Boolean), () => []);
                const idx = raw.correct_index;
                pick(Boolean(!question || options.length < 2 || typeof idx !== "number" || idx < 0 || idx >= options.length), () => {
                }, () => {
                    out.push({
                        question,
                        options,
                        correct_index: idx,
                        explanation: String(raw.explanation ?? "").trim(),
                    });
                });
            }
            catch {
            }
        }
        return out;
    });
}
