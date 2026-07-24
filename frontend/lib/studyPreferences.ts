/** Client-side study preferences (Settings modal). */

export const STUDY_MODE_KEY = "zivo-study-mode";

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

export function writeStudyPreferences(mode: PreferredStudyMode): void {
  try {
    window.localStorage.setItem(STUDY_MODE_KEY, mode);
  } catch {
    /* ignore quota / private mode */
  }
}
