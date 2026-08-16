// @ts-nocheck
"use client";

import { ScrollViewport } from "@/app/_components/ScrollViewport";
import { ProgressView } from "@/app/workspace/_components/ProgressView";
import { useWorkspaceShell } from "@/app/workspace/layout";

/** Lifetime learning journal — reachable from the workspace sidebar. */
export default function WorkspaceProgressPage() {
  const { openAddSource } = useWorkspaceShell();
  return (
    <ScrollViewport>
      <ProgressView onAddSource={openAddSource} />
    </ScrollViewport>
  );
}
