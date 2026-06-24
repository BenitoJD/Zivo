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
  type CSSVariablesResolver,
} from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createQueryClient } from "@/lib/api/query-client";

/** Custom editorial dark scale — warm charcoal tones instead of default grey. */
const DARK_PALETTE = [
  "#FAF7EE", // 0 (Cream text / foreground highlights)
  "#E5E1D3", // 1
  "#C4C0B3", // 2
  "#9D9A8D", // 3
  "#7B786C", // 4
  "#5D5A50", // 5
  "#3E3C36", // 6
  "#2E2D2B", // 7 (Borders)
  "#1C1C1A", // 8 (Card background)
  "#121211", // 9 (Body background)
] as const;

/** Custom editorial brand violet/lilac scale. */
const LAVENDER_PALETTE = [
  "#FAF9FC", // 0
  "#F0ECF7", // 1
  "#E0D7ED", // 2
  "#CBBEE0", // 3
  "#B3A0D1", // 4
  "#967CBD", // 5
  "#7C5EC2", // 6 (Light primary brand color)
  "#6244A6", // 7
  "#4C3285", // 8
  "#372063", // 9
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
    fontFamily: "var(--font-sans), -apple-system, BlinkMacSystemFont, sans-serif",
    headings: {
      fontFamily: "var(--font-serif), Georgia, serif",
      fontWeight: "500",
    },
    primaryColor: "lavender",
    primaryShade: { light: 6, dark: 5 },
    autoContrast: true,
    variantColorResolver,
    defaultRadius: "xl",
    radius: {
      xs: "4px",
      sm: "8px",
      md: "14px",
      lg: "20px",
      xl: "28px",
    },
    colors: {
      dark: [...DARK_PALETTE],
      lavender: [...LAVENDER_PALETTE],
    },
    components: {
      Paper: {
        defaultProps: {
          radius: "xl",
          withBorder: true,
        },
      },
      Button: {
        defaultProps: {
          radius: "xl",
        },
      },
      TextInput: {
        defaultProps: {
          radius: "xl",
        },
      },
      PasswordInput: {
        defaultProps: {
          radius: "xl",
        },
      },
      Textarea: {
        defaultProps: {
          radius: "lg",
        },
      },
      Modal: {
        defaultProps: {
          radius: "xl",
          overlayProps: {
            backgroundOpacity: 0.6,
            blur: 8,
          },
        },
      },
      SegmentedControl: {
        defaultProps: {
          radius: "xl",
        },
      },
    },
  }),
);

const cssVariablesResolver: CSSVariablesResolver = (theme) => ({
  variables: {},
  light: {
    "--mantine-color-body": "#FAF7EE",          // Warm cream background
    "--mantine-color-text": "#1C1C1A",          // Rich dark grey text
    "--mantine-color-default-border": "#E5E1D3",  // Soft warm beige border
    "--mantine-color-default-hover": "#F0EDE2",   // Hover over cream items
    "--mantine-color-placeholder": "#8E8C82",    // Placeholder color
  },
  dark: {
    "--mantine-color-body": "#121211",          // Soft warm off-black background
    "--mantine-color-text": "#FAF7EE",          // Warm cream text
    "--mantine-color-default-border": "#2E2D2B",  // Soft warm charcoal border
    "--mantine-color-default-hover": "#1C1C1A",   // Hover over charcoal items
    "--mantine-color-placeholder": "#605E58",    // Placeholder color
  },
});

export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(createQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <MantineProvider theme={theme} cssVariablesResolver={cssVariablesResolver} defaultColorScheme="dark">
        <Notifications position="top-right" />
        {children}
      </MantineProvider>
    </QueryClientProvider>
  );
}
