"use client";

import type { ReactNode } from "react";
import { Box } from "@mantine/core";

/**
 * Full-viewport scroll container used by every practice route.
 * Replaces the verbatim-identical local `Shell` previously defined per page.
 */
export function Shell({ children }: { children: ReactNode }) {
  return (
    <Box
      bg="var(--mantine-color-body)"
      style={{
        height: "100dvh",
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
