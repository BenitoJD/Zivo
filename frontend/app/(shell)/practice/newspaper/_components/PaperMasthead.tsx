"use client";

/**
 * Thin wrapper kept for any leftover imports — prefer LearnerPageHeader
 * with align="left" compact on practice newspaper pages (matches coding / SD).
 */

import { LearnerPageHeader } from "@/app/_components/study/LearnerPageHeader";

export function PaperMasthead({
  title,
  subtitle,
  compact = true,
}: {
  title: string;
  subtitle?: string;
  compact?: boolean;
}) {
  return (
    <LearnerPageHeader
      align="left"
      eyebrow="Newspaper"
      title={title}
      subtitle={subtitle}
      compact={compact}
    />
  );
}
