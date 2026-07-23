/** Client-side study preferences (Settings modal). */

export const STUDY_MODE_KEY = "zivo-study-mode";
export const MCQ_COUNT_KEY = "zivo-mcq-count";

export type PreferredStudyMode = "relaxed" | "exam";

export function readPreferredStudyMode(): PreferredStudyMode {
  if (typeof window === "undefined") return "relaxed";
  try {
    const v = window.localStorage.getItem(STUDY_MODE_KEY);
    return v === "exam" ? "exam" : "relaxed";
  } catch {
    return "relaxed";
  }
}

/** Exam → Test (graded at end); Relaxed → Learn (instant feedback + tutor). */
export function defaultWorkspaceMode(
  preferred: PreferredStudyMode = readPreferredStudyMode(),
): "learn" | "test" {
  return preferred === "exam" ? "test" : "learn";
}

export function readPreferredMcqCount(): number {
  if (typeof window === "undefined") return 5;
  try {
    const n = parseInt(window.localStorage.getItem(MCQ_COUNT_KEY) || "5", 10);
    if (!Number.isFinite(n)) return 5;
    return Math.min(30, Math.max(3, n));
  } catch {
    return 5;
  }
}

export function writeStudyPreferences(mode: PreferredStudyMode, mcqCount: number): void {
  try {
    window.localStorage.setItem(STUDY_MODE_KEY, mode);
    window.localStorage.setItem(
      MCQ_COUNT_KEY,
      String(Math.min(30, Math.max(3, Math.round(mcqCount) || 5))),
    );
  } catch {
    /* ignore quota / private mode */
  }
}
