"use client";

/**
 * Light paper identity for the newspaper catalog (pick paper / pick day).
 * Same Study-Deck header chrome as other learner hubs — practice itself lives in Learn/Test.
 */

import { LearnerPageHeader } from "@/app/_components/study/LearnerPageHeader";

export function PaperMasthead({
  title,
  subtitle,
  compact = false,
}: {
  title: string;
  subtitle?: string;
  compact?: boolean;
}) {
  return (
    <LearnerPageHeader
      eyebrow="Newspaper"
      title={title}
      subtitle={subtitle}
      compact={compact}
    />
  );
}
