"use client";

import {
  DEFAULT_THEME,
  MantineProvider,
  createTheme,
  defaultVariantColorsResolver,
  getPrimaryShade,
  isLightColor,
  mergeMantineTheme,
  useMantineColorScheme,
  type VariantColorsResolver,
  type CSSVariablesResolver,
} from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { createQueryClient } from "@/lib/api/query-client";
import {
  type MantineColorScheme,
  resolveColorScheme,
  setSsrColorScheme,
  writeColorSchemeCookie,
} from "@/lib/mantine-color-scheme";

/**
 * Calm Paper — a light-first, book-like study aesthetic.
 * Warm paper surfaces, ink text, restrained color, generous air, tactile cards.
 */

/** Neutral paper/ink scale — warm oat to deep ink (not cold grey). */
const NEUTRAL_PALETTE = [
  "#FFFFFF", // 0 — lifted paper (cards)
  "#FAF9F6", // 1 — page (app background)
  "#F3EFE6", // 2 — hover
  "#E7E2D6", // 3 — hairline border
  "#D9D3C4", // 4 — stronger border
  "#A8A296", // 5 — dimmed text
  "#6B675E", // 6 — secondary text
  "#46433D", // 7 — strong muted
  "#232220", // 8 — ink text
  "#1A1917", // 9 — deepest ink
] as const;

/** Brand forest/teal accent — inspired by Wispr Flow (034F46). */
const FOREST_PALETTE = [
  "#F2F7F6", // 0 — lightest tint
  "#E4ECEB", // 1
  "#C5D8D6", // 2
  "#A5C1BD", // 3
  "#7FA39E", // 4
  "#56817B", // 5
  "#2E5E58", // 6
  "#034F46", // 7 — primary forest teal
  "#023D36", // 8
  "#012622", // 9 — deepest forest ink
] as const;

/** Brand lavender accent — kept as a reference fallback. */
const LAVENDER_PALETTE = [
  "#F7F4FB", // 0
  "#EFE9F6", // 1
  "#E1D6EF", // 2
  "#CDBBDD", // 3 — light tint for subtle backgrounds
  "#B3A1CC", // 4
  "#947CB8", // 5
  "#7B5DA6", // 6 — light-scheme primary
  "#644791", // 7 — dark-scheme primary
  "#4E3774", // 8
  "#372259", // 9
] as const;

/** Sage green — "correct / success" feedback, calm not loud. */
const SAGE_PALETTE = [
  "#F1F6EF", "#E3EEDF", "#CBDDC4", "#A8C9A0", "#82AE79",
  "#5F9156", "#4B7A43", "#3A6034", "#2C4A28", "#1F3520",
] as const;

/** Terracotta — "wrong / error" feedback, warm not alarming. */
const TERRACOTTA_PALETTE = [
  "#FBF1ED", "#F4DFD3", "#E8C2AE", "#D69B7D", "#C57454",
  "#AE5634", "#90432A", "#73341F", "#582716", "#3E1B0E",
] as const;

/** Big primary buttons are ink with cream text; lavender fills keep readable contrast. */
const variantColorResolver: VariantColorsResolver = (input) => {
  const resolved = defaultVariantColorsResolver(input);
  const autoContrast =
    typeof input.autoContrast === "boolean" ? input.autoContrast : input.theme.autoContrast;
  if (!autoContrast || (input.variant ?? "filled") !== "filled") return resolved;

  const colorName = input.color || input.theme.primaryColor;
  const palette = input.theme.colors[colorName];
  if (!palette) return resolved;

  const surface = palette[getPrimaryShade(input.theme, resolveColorScheme())];
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
    primaryShade: { light: 7, dark: 4 },
    autoContrast: true,
    variantColorResolver,
    defaultRadius: "xl",
    radius: {
      xs: "6px",
      sm: "10px",
      md: "14px",
      lg: "20px",
      xl: "28px",
    },
    colors: {
      dark: [...NEUTRAL_PALETTE],
      gray: [...NEUTRAL_PALETTE],
      forest: [...FOREST_PALETTE],
      lavender: [...LAVENDER_PALETTE],
      sage: [...SAGE_PALETTE],
      terracotta: [...TERRACOTTA_PALETTE],
      // Keep semantic color scales calm by aliasing them.
      green: [...SAGE_PALETTE],
      red: [...TERRACOTTA_PALETTE],
      blue: [...LAVENDER_PALETTE],
    },
    lineHeights: {
      xs: "1.4",
      sm: "1.5",
      md: "1.6",
      lg: "1.7",
      xl: "1.85",
    },
    shadows: {
      // Soft "paper lift" — calm, not Material-heavy.
      paper: "0 1px 2px rgba(35, 34, 32, 0.04), 0 4px 16px rgba(35, 34, 32, 0.05)",
      "paper-lg": "0 2px 6px rgba(35, 34, 32, 0.06), 0 12px 36px rgba(35, 34, 32, 0.08)",
    },
    components: {
      Paper: {
        defaultProps: {
          radius: "xl",
          withBorder: true,
          shadow: "paper",
        },
      },
      Button: {
        defaultProps: {
          radius: "xl",
        },
      },
      TextInput: {
        defaultProps: {
          radius: "md",
        },
      },
      PasswordInput: {
        defaultProps: {
          radius: "md",
        },
      },
      Textarea: {
        defaultProps: {
          radius: "md",
        },
      },
      Modal: {
        defaultProps: {
          radius: "xl",
          overlayProps: {
            backgroundOpacity: 0.45,
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

const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: {
    "--mantine-color-body": "#FAF9F6",            // Warm oat paper background
    "--mantine-color-text": "#232220",            // Ink text
    "--mantine-color-default-border": "#E7E2D6",  // Warm hairline border
    "--mantine-color-default-hover": "#F3EFE6",   // Hover over paper items
    "--mantine-color-placeholder": "#8E8A7E",     // Placeholder
    "--mantine-skeleton-color": "#F3EFE6",
    "--mantine-skeleton-color-da": "#E7E2D6",
    "--zivo-header-bg": "#FFFFFF",

    // Neutral gray scale overrides for light mode
    "--mantine-color-gray-0": "#FFFFFF",
    "--mantine-color-gray-1": "#FAF9F6",
    "--mantine-color-gray-2": "#F3EFE6",
    "--mantine-color-gray-3": "#E7E2D6",
    "--mantine-color-gray-4": "#D9D3C4",
    "--mantine-color-gray-5": "#A8A296",
    "--mantine-color-gray-6": "#6B675E",
    "--mantine-color-gray-7": "#46433D",
    "--mantine-color-gray-8": "#232220",
    "--mantine-color-gray-9": "#1A1917",

    // Neutral dark scale overrides for light mode (keep aligned with gray)
    "--mantine-color-dark-0": "#FFFFFF",
    "--mantine-color-dark-1": "#FAF9F6",
    "--mantine-color-dark-2": "#F3EFE6",
    "--mantine-color-dark-3": "#E7E2D6",
    "--mantine-color-dark-4": "#D9D3C4",
    "--mantine-color-dark-5": "#A8A296",
    "--mantine-color-dark-6": "#6B675E",
    "--mantine-color-dark-7": "#46433D",
    "--mantine-color-dark-8": "#232220",
    "--mantine-color-dark-9": "#1A1917",
  },
  dark: {
    "--mantine-color-body": "#1A1917",            // Soft warm ink background
    "--mantine-color-text": "#FAF9F6",            // Warm paper text
    "--mantine-color-default-border": "#2E2C28",  // Soft charcoal border
    "--mantine-color-default-hover": "#262522",   // Hover over charcoal items
    "--mantine-color-placeholder": "#7A756B",     // Placeholder
    "--mantine-skeleton-color": "#262522",
    "--mantine-skeleton-color-da": "#2E2C28",
    "--zivo-header-bg": "#22211F",

    // Neutral gray scale overrides for dark mode (inverted logic)
    "--mantine-color-gray-0": "#22211F",          // Lifted paper (dark card background)
    "--mantine-color-gray-1": "#1A1917",          // App background
    "--mantine-color-gray-2": "#2C2B28",          // Hover
    "--mantine-color-gray-3": "#383632",          // Hairline border
    "--mantine-color-gray-4": "#4E4B45",          // Stronger border
    "--mantine-color-gray-5": "#8E8A7E",          // Dimmed text
    "--mantine-color-gray-6": "#A8A296",          // Secondary text
    "--mantine-color-gray-7": "#D9D3C4",          // Strong muted
    "--mantine-color-gray-8": "#FAF9F6",          // Ink text (light)
    "--mantine-color-gray-9": "#FFFFFF",          // Deepest text

    // Neutral dark scale overrides for dark mode (keep aligned with gray)
    "--mantine-color-dark-0": "#22211F",
    "--mantine-color-dark-1": "#1A1917",
    "--mantine-color-dark-2": "#2C2B28",
    "--mantine-color-dark-3": "#383632",
    "--mantine-color-dark-4": "#4E4B45",
    "--mantine-color-dark-5": "#8E8A7E",
    "--mantine-color-dark-6": "#A8A296",
    "--mantine-color-dark-7": "#D9D3C4",
    "--mantine-color-dark-8": "#FAF9F6",
    "--mantine-color-dark-9": "#FFFFFF",
  },
});

function ColorSchemeCookieSync() {
  const { colorScheme } = useMantineColorScheme();
  useEffect(() => {
    writeColorSchemeCookie(colorScheme === "dark" ? "dark" : "light");
  }, [colorScheme]);
  return null;
}

export default function Providers({
  children,
  colorScheme = "light",
}: {
  children: React.ReactNode;
  colorScheme?: MantineColorScheme;
}) {
  setSsrColorScheme(colorScheme);
  const [queryClient] = useState(createQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <MantineProvider
        theme={theme}
        cssVariablesResolver={cssVariablesResolver}
        defaultColorScheme={colorScheme}
      >
        <ColorSchemeCookieSync />
        <Notifications position="top-right" />
        {children}
      </MantineProvider>
    </QueryClientProvider>
  );
}
