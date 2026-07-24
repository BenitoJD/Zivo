"use client";

import { Box } from "@mantine/core";
import { ProgressView } from "@/app/workspace/_components/ProgressView";
import { useWorkspaceShell } from "@/app/workspace/layout";

/** Lifetime learning journal — reachable from the workspace sidebar. */
export default function WorkspaceProgressPage() {
  const { openAddSource } = useWorkspaceShell();
  return (
    <Box
      flex={1}
      style={{
        overflowY: "auto",
        overflowX: "hidden",
        WebkitOverflowScrolling: "touch",
        background: "var(--mantine-color-body)",
      }}
    >
      <ProgressView onAddSource={openAddSource} />
    </Box>
  );
}
