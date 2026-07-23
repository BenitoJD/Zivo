/**
 * Shared layout constants + pure helpers + types for the workspace study view.
 * Extracted from the page so the panel/grid/selection clusters (page selection,
 * source panel, end-of-study screens, mobile shell) can all share one source of
 * truth instead of reaching into the page module. No React, no side effects.
 */

import { MOBILE_MAX_MQ, STUDY_DESKTOP_MQ } from "@/lib/responsive";

// --- 3-pane panel sizing -----------------------------------------------------
export const SOURCE_PANEL_DEFAULT = 360;
export const SOURCE_PANEL_MIN = 280;
export const SOURCE_PANEL_MAX = 720;
export const TUTOR_PANEL_DEFAULT = 400;
export const TUTOR_PANEL_MIN = 300;
export const TUTOR_PANEL_MAX = 560;
export const STUDY_CENTER_MIN = 380;
export const PANEL_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";
export const PANEL_MS = 280;

// --- responsive breakpoints --------------------------------------------------
// Desktop 3-pane only ≥992px (62em); below that the clean single-column mobile
// shell is used - the cramped 3-pane didn't fit small tablets / large phones.
export const STUDY_DESKTOP_BP = STUDY_DESKTOP_MQ;
export const STUDY_COMPACT_BP = MOBILE_MAX_MQ;
/** @deprecated Prefer STUDY_COMPACT_BP — same range as mobile AppShell. */
export const STUDY_OVERLAY_BP = "(max-width: 61.99em)";

// --- thumbnail grid ----------------------------------------------------------
export const THUMB_GAP = 28;
export const THUMB_GAP_COMPACT = 14;
export const THUMB_MIN_WIDTH = 248;
export const THUMB_MIN_WIDTH_COMPACT = 148;
export const THUMB_MAX_WIDTH = 320;
export const THUMB_MAX_COLS = 4;
export const THUMB_MAX_COLS_COMPACT = 2;

// --- page-selection screen ---------------------------------------------------
export const SELECTION_PAD_X = 28;
export const SELECTION_PAD_Y = 16;
export const SELECTION_PAD_Y_COMPACT = 10;
export const SELECTION_PAD_X_COMPACT = 16;
export const SELECTION_DOCK_WIDTH = "80%";
export const SELECTION_DOCK_RESERVE = 156;
export const SELECTION_DOCK_RESERVE_COMPACT = 168;
export const THUMB_FRAME_ASPECT = 1.414;
export const THUMB_FRAME_ASPECT_COMPACT = 1.414;

export function shellBleedPx(compact: boolean): number {
  return compact ? 0 : 16;
}

export function computeGridLayout(
  gridWidth: number,
  minThumbWidth: number,
  gap: number,
  maxCols: number,
  maxThumbWidth = THUMB_MAX_WIDTH,
) {
  if (gridWidth < 1) return { cols: 2, thumbWidth: minThumbWidth };
  const cols = Math.min(
    maxCols,
    Math.max(1, Math.floor((gridWidth + gap) / (minThumbWidth + gap))),
  );
  const totalGap = gap * Math.max(0, cols - 1);
  const thumbWidth = Math.min(maxThumbWidth, Math.floor((gridWidth - totalGap) / cols));
  return { cols, thumbWidth };
}

export function clampPanel(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

export function pagesInRange(from: number, to: number): number[] {
  const lo = Math.min(from, to);
  const hi = Math.max(from, to);
  const pages: number[] = [];
  for (let p = lo; p <= hi; p += 1) pages.push(p);
  return pages;
}

/** "1 page" / "N pages" — avoid "1 pages" in picker chrome. */
export function formatPageCountLabel(n: number): string {
  return `${n} ${n === 1 ? "page" : "pages"}`;
}

export function formatSelectionSummary(selectedPages: number[], pageCount: number): string {
  if (selectedPages.length === 0) return `No pages selected · ${pageCount} total`;
  if (selectedPages.length === 1) return `1 page selected · page ${selectedPages[0]}`;
  if (selectedPages.length === pageCount) return `All ${formatPageCountLabel(pageCount)} selected`;
  const first = selectedPages[0];
  const last = selectedPages[selectedPages.length - 1];
  const contiguous = selectedPages.length === last - first + 1;
  if (contiguous) return `${selectedPages.length} pages selected · ${first}-${last}`;
  return `${selectedPages.length} pages selected`;
}

/** A question the learner has already answered - kept client-side so they can step
 *  back and review any prior answer (with the choice they made + the explanation). */
export type AnsweredCard = {
  assertionId: string;
  stem: string;
  options: string[];
  selectedIndex: number;
  /** Full chosen set for multi-select ("select all that apply") reviews. */
  selectedIndices?: number[];
  gradeState: { correct: boolean; correctIndex: number; correctIndices?: number[] };
  feedback: string | null;
  /** The concept/topic this question tested - for the end-of-study report card. */
  concept?: string | null;
  /** Whether the FIRST attempt was correct (Learn lets you retry; the report card
   *  needs the first try to surface genuinely weak topics, not post-retry success). */
  firstTryCorrect: boolean;
};
