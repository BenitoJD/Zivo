/** Display formatting for UPSC-style statement stems (inline "1." "2." lists). */

import { sanitizeMcqStem } from "@/lib/types";

const NUMBERED_STATEMENT = /\b\d{1,2}\.\s/g;

const TRAILING_QUESTION =
  /\s+(Which of the (?:following )?statements(?: given above)?|Which one of the following|Assertion and Reason)/i;

/** Split inline numbered statements onto separate lines for readable stems. */
export function formatMcqStemForDisplay(text: string): string {
  const stem = sanitizeMcqStem(text);
  if (!stem) return "";
  if (stem.includes("\n")) return stem;

  const hits = stem.match(NUMBERED_STATEMENT);
  if (!hits || hits.length < 1) return stem;

  let out = stem;
  out = out.replace(/([:;])\s+(?=\d{1,2}\.\s)/, "$1\n\n");
  out = out.replace(/(?<=\S)\s+(?=\d{1,2}\.\s)/g, "\n\n");
  out = out.replace(TRAILING_QUESTION, "\n\n$1");
  return out.trim();
}

/** True when the stem reads as a multi-line statement block (left-align in UI). */
export function isStatementStyleStem(text: string): boolean {
  return formatMcqStemForDisplay(text).includes("\n");
}
