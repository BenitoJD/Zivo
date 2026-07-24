"use client";

import type { ReactNode } from "react";
import { Box } from "@mantine/core";

/**
 * Scroll surface inside the shared learner AppShell.
 * Fills AppShell.Main (flex column); do not use 100dvh — body is already locked.
 */
export function Shell({ children }: { children: ReactNode }) {
  return (
    <Box
      flex={1}
      mih={0}
      bg="var(--mantine-color-body)"
      style={{
        overflowY: "auto",
        overflowX: "hidden",
        WebkitOverflowScrolling: "touch",
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
      }}
    >
      {children}
    </Box>
  );
}
