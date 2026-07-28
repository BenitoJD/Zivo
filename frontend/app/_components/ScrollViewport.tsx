"use client";

import type { ReactNode } from "react";
import { Box, type BoxProps } from "@mantine/core";

type ScrollViewportProps = BoxProps & {
  children?: ReactNode;
  /**
   * `flex` — fill AppShell.Main and scroll inside (workspace / practice routes).
   * `viewport` — full viewport scroll for public routes (body is overflow:hidden).
   */
  variant?: "flex" | "viewport";
};

/** Primary scroll surface when the root body is locked to 100dvh. */
export function ScrollViewport({
  children,
  variant = "flex",
  bg = "var(--mantine-color-body)",
  ...rest
}: ScrollViewportProps) {
  const isViewport = variant === "viewport";

  return (
    <Box
      bg={bg}
      flex={isViewport ? undefined : 1}
      mih={isViewport ? undefined : 0}
      miw={0}
      {...rest}
      style={{
        ...(isViewport ? { height: "100dvh", minHeight: "100dvh" } : {}),
        overflowY: "auto",
        overflowX: "hidden",
        WebkitOverflowScrolling: "touch",
        overscrollBehaviorY: "contain",
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
        ...(typeof rest.style === "object" && rest.style !== null ? rest.style : {}),
      }}
    >
      {children}
    </Box>
  );
}
