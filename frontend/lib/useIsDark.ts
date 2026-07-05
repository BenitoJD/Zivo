"use client";

import { useMantineColorScheme } from "@mantine/core";

/**
 * Convenience hook: returns true when the active Mantine color scheme is dark.
 *
 * Replaces the 9× copy-pasted `const { colorScheme } = useMantineColorScheme();
 * const isDark = colorScheme === "dark"` two-liner across workspace components.
 */
export function useIsDark(): boolean {
  const { colorScheme } = useMantineColorScheme();
  return colorScheme === "dark";
}
