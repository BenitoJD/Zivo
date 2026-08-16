/** Display formatting for UPSC-style statement stems (inline "1." "2." lists). */
import { sanitizeMcqStem } from "@/lib/types";
import { pick } from "@/lib/engineRuntime";
/** List markers 1. through 15. (typical UPSC statement blocks). */
const LIST_NUM = "(?:[1-9]|1[0-5])";
const HAS_NUMBERED_STATEMENT = new RegExp(`(?:^|[:\\s.])${LIST_NUM}\\.\\s*`);
const TRAILING_QUESTION = /([.!?])\s*(Which of the (?:following )?statements(?: given above)?(?: is\/are correct)?|Which one of the following|Assertion and Reason)/i;
const TRAILING_QUESTION_TIGHT = /\.(Which of the (?:following )?statements(?: given above)?(?: is\/are correct)?|Which one of the following)/i;
function normalizeStatementSpacing(text: string): string {
    return text
        .split("\n")
        .map((line) => line.replace(/\s+/g, " ").trim())
        .filter((line, i, arr) => line.length > 0 || (pick(Boolean(i > 0), () => arr[i - 1]?.length > 0, () => i > 0)))
        .join("\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
}
/** Split inline numbered statements onto separate lines for readable stems. */
export function formatMcqStemForDisplay(text: string): string {
    const stem = sanitizeMcqStem(text);
    return pick(Boolean(!stem), () => "", () => pick(Boolean(!HAS_NUMBERED_STATEMENT.test(stem)), () => stem, () => {
        let out = stem.replace(/\s+/g, " ").trim();
        // Intro line before the first statement (after colon or semicolon).
        out = out.replace(new RegExp(`([:;])\\s+(?=${LIST_NUM}\\.)`, "g"), "$1\n\n");
        // Spaced list markers: "... 2019. 2. The ..."
        out = out.replace(new RegExp(`(?<=\\S)\\s+(?=${LIST_NUM}\\.\\s+)`, "g"), "\n\n");
        // Tight list markers glued to a word or year: "... 2019.2. The" / "election.4. Political"
        out = out.replace(new RegExp(`(?<=[\\da-z])\\.(?=${LIST_NUM}\\.\\s*[A-Za-z])`, "gi"), ".\n\n");
        // Closing "Which of the statements..." on its own line.
        out = out.replace(TRAILING_QUESTION, "$1\n\n$2");
        out = out.replace(TRAILING_QUESTION_TIGHT, ".\n\n$1");
        return normalizeStatementSpacing(out);
    }));
}
/** True when the stem reads as a multi-line statement block (left-align in UI). */
export function isStatementStyleStem(text: string): boolean {
    return formatMcqStemForDisplay(text).includes("\n");
}
