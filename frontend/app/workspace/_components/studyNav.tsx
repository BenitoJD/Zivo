"use client";

import { createContext, useContext, useMemo, useState } from "react";

/** Every study mode the artifact view can show. */
export type StudyMode =
  | "learn"
  | "test"
  | "explain"
  | "notes"
  | "cards"
  | "palace"
  | "read"
  | "quiz"
  | "interview"
  | "resume"
  | "coding";

type StudyNavValue = {
  /** True while an artifact study view is mounted (so the sidebar shows modes). */
  active: boolean;
  setActive: (active: boolean) => void;
  mode: StudyMode;
  setMode: (mode: StudyMode) => void;
};

const StudyNavContext = createContext<StudyNavValue | null>(null);

/**
 * Lifts the study-mode selection up to the workspace layout so the *global*
 * left sidebar (which lives beside, not inside, the artifact page) can host the
 * mode navigator below the source list — one rail instead of two.
 */
export function StudyNavProvider({ children }: { children: React.ReactNode }) {
  const [active, setActive] = useState(false);
  const [mode, setMode] = useState<StudyMode>("learn");
  const value = useMemo(() => ({ active, setActive, mode, setMode }), [active, mode]);
  return <StudyNavContext.Provider value={value}>{children}</StudyNavContext.Provider>;
}

export function useStudyNav() {
  const ctx = useContext(StudyNavContext);
  if (!ctx) throw new Error("useStudyNav must be used within a StudyNavProvider");
  return ctx;
}
