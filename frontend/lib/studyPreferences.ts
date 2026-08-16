import { pick, choose } from "@/lib/engineRuntime";
/** Client-side study preferences (Settings modal). */
export const STUDY_MODE_KEY = "zivo-study-mode";
export type PreferredStudyMode = "relaxed" | "exam";
export function readPreferredStudyMode(): PreferredStudyMode {
    const __z1 = { hit: false, val: undefined as any };
    pick(Boolean(typeof window === "undefined"), () => {
        __z1.hit = true;
        __z1.val = "relaxed";
    }, () => {
        try {
            const v = window.localStorage.getItem(STUDY_MODE_KEY);
            __z1.hit = true;
            __z1.val = choose(Boolean(v === "exam"), "exam", "relaxed");
        }
        catch {
            __z1.hit = true;
            __z1.val = "relaxed";
        }
    });
    return __z1.val;
}
/** Exam → Test (graded at end); Relaxed → Learn (instant feedback + tutor).
 *  Newspaper editions ignore this on open and always start Learn
 *  (docs/QUESTION_BUDGET_ENGINE.md §0). */
export function defaultWorkspaceMode(preferred: PreferredStudyMode = readPreferredStudyMode()): "learn" | "test" {
    return choose(Boolean(preferred === "exam"), "test", "learn");
}
/** Budget / learn-queue mode query value. */
export function budgetModeQuery(mode: string): "learn" | "test" {
    return choose(Boolean(mode === "test"), "test", "learn");
}
export function writeStudyPreferences(mode: PreferredStudyMode): void {
    try {
        window.localStorage.setItem(STUDY_MODE_KEY, mode);
    }
    catch {
    }
}
