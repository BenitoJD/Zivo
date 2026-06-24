"use client";

import {
  DEFAULT_THEME,
  MantineProvider,
  createTheme,
  defaultVariantColorsResolver,
  getPrimaryShade,
  isLightColor,
  mergeMantineTheme,
  type VariantColorsResolver,
} from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createQueryClient } from "@/lib/api/query-client";

/** Mantine default dark scale — borders, modals, inputs, and disabled states stay readable. */
const DARK_PALETTE = [
  "#C1C2C5",
  "#A6A7AB",
  "#909296",
  "#5C5F66",
  "#373A40",
  "#2C2E33",
  "#25262B",
  "#1A1B1E",
  "#141517",
  "#101113",
] as const;

function resolvedColorScheme(): "light" | "dark" {
  if (typeof document === "undefined") return "dark";
  const scheme = document.documentElement.getAttribute("data-mantine-color-scheme");
  return scheme === "light" ? "light" : "dark";
}

/** Gray primary fills are light in dark mode — force dark text on those surfaces. */
const variantColorResolver: VariantColorsResolver = (input) => {
  const resolved = defaultVariantColorsResolver(input);
  const autoContrast =
    typeof input.autoContrast === "boolean" ? input.autoContrast : input.theme.autoContrast;
  if (!autoContrast || (input.variant ?? "filled") !== "filled") return resolved;

  const colorName = input.color || input.theme.primaryColor;
  const palette = input.theme.colors[colorName];
  if (!palette) return resolved;

  const surface = palette[getPrimaryShade(input.theme, resolvedColorScheme())];
  if (!isLightColor(surface, input.theme.luminanceThreshold ?? 0.3)) return resolved;

  return {
    ...resolved,
    color: "var(--mantine-color-black)",
    hoverColor: "var(--mantine-color-black)",
  };
};

const theme = mergeMantineTheme(
  DEFAULT_THEME,
  createTheme({
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", Inter, system-ui, sans-serif',
    headings: {
      fontFamily:
        '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", Inter, system-ui, sans-serif',
      fontWeight: "600",
    },
    primaryColor: "gray",
    primaryShade: { light: 6, dark: 4 },
    autoContrast: true,
    variantColorResolver,
    defaultRadius: "lg",
    colors: {
      dark: [...DARK_PALETTE],
    },
  }),
);

export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(createQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <MantineProvider theme={theme} defaultColorScheme="dark">
        <Notifications position="top-right" />
        {children}
      </MantineProvider>
    </QueryClientProvider>
  );
}
