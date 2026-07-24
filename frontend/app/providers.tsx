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
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { createQueryClient } from "@/lib/api/query-client";
import {
  type MantineColorScheme,
  cookieColorSchemeManager,
  isLightOnlyPath,
  MANTINE_COLOR_SCHEME_STORAGE_KEY,
  readColorSchemeCookieClient,
  resolveColorScheme,
  setSsrColorScheme,
  writeColorSchemeCookie,
} from "@/lib/mantine-color-scheme";

/**
 * Calm Paper - a light-first, book-like study aesthetic.
 * Warm paper surfaces, ink text, restrained color, generous air, tactile cards.
 */

/** Neutral paper/ink scale - warm oat to deep ink (not cold grey). */
const NEUTRAL_PALETTE = [
  "#FFFFFF", // 0 - lifted paper (cards)
  "#FAF9F6", // 1 - page (app background)
  "#F3EFE6", // 2 - hover
  "#E7E2D6", // 3 - hairline border
  "#D9D3C4", // 4 - stronger border
  "#A8A296", // 5 - dimmed text
  "#6B675E", // 6 - secondary text
  "#46433D", // 7 - strong muted
  "#232220", // 8 - ink text
  "#1A1917", // 9 - deepest ink
] as const;

/** Brand forest/teal accent - inspired by Wispr Flow (034F46). */
const FOREST_PALETTE = [
  "#F2F7F6", // 0 - lightest tint
  "#E4ECEB", // 1
  "#C5D8D6", // 2
  "#A5C1BD", // 3
  "#7FA39E", // 4
  "#56817B", // 5
  "#2E5E58", // 6
  "#034F46", // 7 - primary forest teal
  "#023D36", // 8
  "#012622", // 9 - deepest forest ink
] as const;

/** Brand lavender accent - kept as a reference fallback. */
const LAVENDER_PALETTE = [
  "#F7F4FB", // 0
  "#EFE9F6", // 1
  "#E1D6EF", // 2
  "#CDBBDD", // 3 - light tint for subtle backgrounds
  "#B3A1CC", // 4
  "#947CB8", // 5
  "#7B5DA6", // 6 - light-scheme primary
  "#644791", // 7 - dark-scheme primary
  "#4E3774", // 8
  "#372259", // 9
] as const;

/** Sage green - "correct / success" feedback, calm not loud. */
const SAGE_PALETTE = [
  "#F1F6EF", "#E3EEDF", "#CBDDC4", "#A8C9A0", "#82AE79",
  "#5F9156", "#4B7A43", "#3A6034", "#2C4A28", "#1F3520",
] as const;

/** Terracotta - "wrong / error" feedback, warm not alarming. */
const TERRACOTTA_PALETTE = [
  "#FBF1ED", "#F4DFD3", "#E8C2AE", "#D69B7D", "#C57454",
  "#AE5634", "#90432A", "#73341F", "#582716", "#3E1B0E",
] as const;

/** Brand + semantic accents whose dark CSS vars invert low/high shades. */
const REMAPPED_ACCENTS = new Set([
  "lavender",
  "forest",
  "sage",
  "terracotta",
  "green",
  "red",
  "blue",
  "orange",
  "indigo",
  "grape",
  "teal",
  "cyan",
  "pink",
  "violet",
  "lime",
  "yellow",
]);

/** Big primary buttons are ink with cream text; lavender fills keep readable contrast. */
const variantColorResolver: VariantColorsResolver = (input) => {
  const resolved = defaultVariantColorsResolver(input);
  const variant = input.variant ?? "filled";
  const colorName = input.color || input.theme.primaryColor;
  const scheme = resolveColorScheme();

  // Ghost gray buttons (subtle/light/transparent/default) get their label color from
  // low gray shades. Our gray scale is INVERTED for dark mode (low = dark), so Mantine
  // computes dark-on-dark labels - which is why "Settings", "Sign in", "Upload file",
  // etc. went invisible at night. Force a readable, scheme-aware label for gray ghosts.
  if (
    (colorName === "gray" || colorName === "dark") &&
    (variant === "subtle" || variant === "light" || variant === "transparent" || variant === "default")
  ) {
    return {
      ...resolved,
      color: "var(--mantine-color-text)",
      hoverColor: "var(--mantine-color-text)",
    };
  }

  // Same class of bug for remapped accents: Mantine's dark `*-light-color` picks
  // shade 0 (deep surface in our remap) as the label. Point light/subtle/outline
  // labels at the bright text-on-ink shade instead.
  if (
    scheme === "dark" &&
    REMAPPED_ACCENTS.has(colorName) &&
    (variant === "subtle" || variant === "light" || variant === "transparent" || variant === "outline")
  ) {
    return {
      ...resolved,
      color: `var(--mantine-color-${colorName}-7)`,
      hoverColor: `var(--mantine-color-${colorName}-8)`,
    };
  }

  const autoContrast =
    typeof input.autoContrast === "boolean" ? input.autoContrast : input.theme.autoContrast;
  if (!autoContrast || variant !== "filled") return resolved;

  const palette = input.theme.colors[colorName];
  if (!palette) return resolved;

  const surface = palette[getPrimaryShade(input.theme, scheme)];
  if (!isLightColor(surface, input.theme.luminanceThreshold ?? 0.3)) return resolved;

  // The button surface is light, so its label must be dark ink for contrast.
  // Use a literal ink rather than gray-9 / black tokens - those are inverted to
  // near-white in dark mode, which is exactly what made dark-mode buttons unreadable.
  const onLightSurface = "#1A1917";

  return {
    ...resolved,
    color: onLightSurface,
    hoverColor: onLightSurface,
  };
};

const theme = mergeMantineTheme(
  DEFAULT_THEME,
  createTheme({
    fontFamily: "var(--font-sans), -apple-system, BlinkMacSystemFont, sans-serif",
    headings: {
      fontFamily: "'Newsreader', 'EB Garamond', Georgia, serif",
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
      // Soft "paper lift" - calm, not Material-heavy. Dark values are overridden
      // in cssVariablesResolver so cards still lift on ink.
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
      Tooltip: {
        styles: {
          tooltip: {
            backgroundColor: "var(--zivo-tooltip-bg)",
            color: "var(--zivo-tooltip-fg)",
            border: "1px solid var(--mantine-color-default-border)",
            boxShadow: "var(--mantine-shadow-paper)",
          },
          arrow: {
            backgroundColor: "var(--zivo-tooltip-bg)",
            border: "1px solid var(--mantine-color-default-border)",
          },
        },
      },
      Notification: {
        defaultProps: { radius: "xl", withBorder: false },
        // Premium frosted-glass toast: a soft blurred surface that lifts off the
        // page with layered shadow + a fine top highlight, and a refined inset
        // rounded color accent (not a loud full-height bar). Calm in both schemes.
        styles: {
          root: {
            background: "color-mix(in srgb, var(--mantine-color-body) 80%, transparent)",
            backdropFilter: "blur(20px) saturate(1.5)",
            WebkitBackdropFilter: "blur(20px) saturate(1.5)",
            border: "1px solid var(--mantine-color-default-border)",
            boxShadow:
              "0 18px 50px rgba(20, 19, 16, 0.18), 0 4px 14px rgba(20, 19, 16, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.16)",
            padding: "13px 14px 13px 18px",
            "&::before": {
              left: 8,
              top: 13,
              bottom: 13,
              width: 3,
              borderRadius: 999,
            },
          },
          title: {
            fontWeight: 600,
            fontSize: "0.9rem",
            letterSpacing: "-0.01em",
            color: "var(--mantine-color-text)",
          },
          description: {
            fontSize: "0.82rem",
            lineHeight: 1.45,
            color: "var(--mantine-color-dimmed)",
          },
          icon: { borderRadius: "var(--mantine-radius-md)" },
          closeButton: {
            color: "var(--mantine-color-dimmed)",
            borderRadius: "var(--mantine-radius-md)",
          },
        },
      },
    },
  }),
);

const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: {
    "--mantine-color-body": "#FCFBEF",            // Warm ivory cream (Wispr-matched)
    "--mantine-color-text": "#232220",            // Ink text
    "--mantine-color-default-border": "#E7E2D6",  // Warm hairline border
    "--mantine-color-default-hover": "#F3EFE6",   // Hover over paper items
    "--mantine-color-placeholder": "#8E8A7E",     // Placeholder
    "--mantine-color-dimmed": "#6B675E",          // Secondary labels
    "--mantine-skeleton-color": "#F3EFE6",
    "--mantine-skeleton-color-da": "#E7E2D6",
    "--zivo-header-bg": "#FFFEFA",
    "--zivo-tooltip-bg": "#232220",
    "--zivo-tooltip-fg": "#FCFBEF",

    // Neutral gray scale overrides for light mode - warm ivory page, warm-white cards.
    "--mantine-color-gray-0": "#FFFEFA",
    "--mantine-color-gray-1": "#FCFBEF",
    "--mantine-color-gray-2": "#F3EFE6",
    "--mantine-color-gray-3": "#E7E2D6",
    "--mantine-color-gray-4": "#D9D3C4",
    "--mantine-color-gray-5": "#A8A296",
    "--mantine-color-gray-6": "#6B675E",
    "--mantine-color-gray-7": "#46433D",
    "--mantine-color-gray-8": "#232220",
    "--mantine-color-gray-9": "#1A1917",

    // Neutral dark scale overrides for light mode (keep aligned with gray)
    "--mantine-color-dark-0": "#FFFEFA",
    "--mantine-color-dark-1": "#FCFBEF",
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
    "--mantine-color-black": "#FAF9F6",           // Never ink-black text on dark surfaces
    "--mantine-color-white": "#FFFFFF",
    "--mantine-color-default-border": "#2E2C28",  // Soft charcoal border
    "--mantine-color-default-hover": "#262522",   // Hover over charcoal items
    "--mantine-color-placeholder": "#8E8A7E",     // Placeholder (warmer, matches gray-5)
    "--mantine-color-dimmed": "#A8A296",          // Secondary labels - warm, readable on ink
    "--mantine-skeleton-color": "#262522",
    "--mantine-skeleton-color-da": "#2E2C28",
    "--zivo-header-bg": "#22211F",
    "--zivo-tooltip-bg": "#2E2C28",
    "--zivo-tooltip-fg": "#FAF9F6",
    // Paper lift must stay visible on ink - light-scheme soft browns vanish at night.
    "--mantine-shadow-paper": "0 1px 2px rgba(0, 0, 0, 0.28), 0 4px 18px rgba(0, 0, 0, 0.32)",
    "--mantine-shadow-paper-lg": "0 2px 8px rgba(0, 0, 0, 0.32), 0 14px 40px rgba(0, 0, 0, 0.4)",

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

    // Mantine SURFACE scale for dark mode. Unlike `gray` (which the app inverts so
    // high indices read as light TEXT), `dark.N` is used by Mantine internals AND
    // the app's `isDark ? "dark.7"` surfaces as real backgrounds - so it must run
    // normal: low = light, high = deep ink. This is what fixes pale inputs/panels.
    "--mantine-color-dark-0": "#C9C7BE",
    "--mantine-color-dark-1": "#A8A296",
    "--mantine-color-dark-2": "#8E8A7E",
    "--mantine-color-dark-3": "#6B675E",
    "--mantine-color-dark-4": "#46433D",
    "--mantine-color-dark-5": "#2C2B28",
    "--mantine-color-dark-6": "#232220",
    "--mantine-color-dark-7": "#1F1E1C",
    "--mantine-color-dark-8": "#1A1917",
    "--mantine-color-dark-9": "#141310",
    "--mantine-color-default": "#232220",

    // Forest accent - dark-mode remap. The light scale's low shades (#F2F7F6…)
    // are near-white and were glowing as bright patches on the dark page. In dark
    // mode the low shades become deep green-tinted SURFACES, and the high shades
    // BRIGHTEN so accent text / icons keep contrast on ink. Solid-green buttons
    // hardcode #034F46 directly, so brightening 7/8 here is safe for them.
    "--mantine-color-forest-0": "#15241F",  // deep tint surface (accent bg)
    "--mantine-color-forest-1": "#1A302A",  // raised tint surface
    "--mantine-color-forest-2": "#234039",  // accent border
    "--mantine-color-forest-3": "#2E5148",  // stronger accent border
    "--mantine-color-forest-4": "#7FA39E",  // primary button bg (autoContrast)
    "--mantine-color-forest-5": "#8FB3AD",
    "--mantine-color-forest-6": "#A3C4BE",  // accent text / icon
    "--mantine-color-forest-7": "#B3D1CB",  // primary accent text on ink
    "--mantine-color-forest-8": "#CBE0DC",
    "--mantine-color-forest-9": "#E4ECEB",

    // Lavender (primary) - dark-mode remap, same approach as forest: low shades
    // become deep lavender-tinted surfaces/borders, high shades brighten so
    // accent text and primary buttons keep contrast on ink.
    "--mantine-color-lavender-0": "#1E1A2A",  // deep tint surface
    "--mantine-color-lavender-1": "#262034",  // raised tint surface
    "--mantine-color-lavender-2": "#332B45",  // accent border
    "--mantine-color-lavender-3": "#433A5C",
    "--mantine-color-lavender-4": "#9C86C8",  // primary button bg (autoContrast)
    "--mantine-color-lavender-5": "#AB97D2",
    "--mantine-color-lavender-6": "#BCABDD",  // accent text / icon
    "--mantine-color-lavender-7": "#CDBFEA",  // primary accent text on ink
    "--mantine-color-lavender-8": "#DDD2F2",
    "--mantine-color-lavender-9": "#EFE9F8",

    // Sage - dark-mode remap (feedback + correct-option surfaces)
    "--mantine-color-sage-0": "#1A2B1F",
    "--mantine-color-sage-1": "#243628",
    "--mantine-color-sage-2": "#2E4535",
    "--mantine-color-sage-3": "#3A5843",
    "--mantine-color-sage-4": "#5F9156",
    "--mantine-color-sage-5": "#82AE79",
    "--mantine-color-sage-6": "#A8C9A0",
    "--mantine-color-sage-7": "#C5D8BE",
    "--mantine-color-sage-8": "#DDEAD6",
    "--mantine-color-sage-9": "#EEF5EB",

    // Terracotta - dark-mode remap (wrong-option + error feedback)
    "--mantine-color-terracotta-0": "#2B1A14",
    "--mantine-color-terracotta-1": "#3A2319",
    "--mantine-color-terracotta-2": "#4A2E22",
    "--mantine-color-terracotta-3": "#5E3B2C",
    "--mantine-color-terracotta-4": "#C57454",
    "--mantine-color-terracotta-5": "#D69B7D",
    "--mantine-color-terracotta-6": "#E8B09A",
    "--mantine-color-terracotta-7": "#F0C4B2",
    "--mantine-color-terracotta-8": "#F7D8CC",
    "--mantine-color-terracotta-9": "#FBECE6",

    // Semantic aliases must follow the same dark remap as their Calm Paper
    // counterparts - otherwise `color="green"|"red"|"blue"` still paints
    // near-white shade-0/1 chips on ink (Settings, Alerts, legacy props).
    "--mantine-color-green-0": "#1A2B1F",
    "--mantine-color-green-1": "#243628",
    "--mantine-color-green-2": "#2E4535",
    "--mantine-color-green-3": "#3A5843",
    "--mantine-color-green-4": "#5F9156",
    "--mantine-color-green-5": "#82AE79",
    "--mantine-color-green-6": "#A8C9A0",
    "--mantine-color-green-7": "#C5D8BE",
    "--mantine-color-green-8": "#DDEAD6",
    "--mantine-color-green-9": "#EEF5EB",

    "--mantine-color-red-0": "#2B1A14",
    "--mantine-color-red-1": "#3A2319",
    "--mantine-color-red-2": "#4A2E22",
    "--mantine-color-red-3": "#5E3B2C",
    "--mantine-color-red-4": "#C57454",
    "--mantine-color-red-5": "#D69B7D",
    "--mantine-color-red-6": "#E8B09A",
    "--mantine-color-red-7": "#F0C4B2",
    "--mantine-color-red-8": "#F7D8CC",
    "--mantine-color-red-9": "#FBECE6",

    "--mantine-color-blue-0": "#1E1A2A",
    "--mantine-color-blue-1": "#262034",
    "--mantine-color-blue-2": "#332B45",
    "--mantine-color-blue-3": "#433A5C",
    "--mantine-color-blue-4": "#9C86C8",
    "--mantine-color-blue-5": "#AB97D2",
    "--mantine-color-blue-6": "#BCABDD",
    "--mantine-color-blue-7": "#CDBFEA",
    "--mantine-color-blue-8": "#DDD2F2",
    "--mantine-color-blue-9": "#EFE9F8",

    // Sidebar / Settings still use a few stock Mantine hues for mode icons.
    // Remap the same way: low = deep tint surfaces, high = bright glyphs on ink.
    // Without this, variant="light" ThemeIcons blast near-white chips at night.
    "--mantine-color-orange-0": "#2A1C12",
    "--mantine-color-orange-1": "#3A2618",
    "--mantine-color-orange-2": "#4A3220",
    "--mantine-color-orange-3": "#5E412A",
    "--mantine-color-orange-4": "#D98A4A",
    "--mantine-color-orange-5": "#E4A06A",
    "--mantine-color-orange-6": "#EBB888",
    "--mantine-color-orange-7": "#F2CBA6",
    "--mantine-color-orange-8": "#F7DEC4",
    "--mantine-color-orange-9": "#FBF0E4",

    "--mantine-color-indigo-0": "#1A1C2E",
    "--mantine-color-indigo-1": "#22253A",
    "--mantine-color-indigo-2": "#2E3250",
    "--mantine-color-indigo-3": "#3C4166",
    "--mantine-color-indigo-4": "#7480C8",
    "--mantine-color-indigo-5": "#8A95D4",
    "--mantine-color-indigo-6": "#A0ABDE",
    "--mantine-color-indigo-7": "#B8C0E8",
    "--mantine-color-indigo-8": "#D0D5F0",
    "--mantine-color-indigo-9": "#E8EAF8",

    "--mantine-color-grape-0": "#241828",
    "--mantine-color-grape-1": "#302034",
    "--mantine-color-grape-2": "#3E2A44",
    "--mantine-color-grape-3": "#503656",
    "--mantine-color-grape-4": "#B07AC8",
    "--mantine-color-grape-5": "#C090D4",
    "--mantine-color-grape-6": "#CEA6DE",
    "--mantine-color-grape-7": "#DCBCE8",
    "--mantine-color-grape-8": "#EAD2F2",
    "--mantine-color-grape-9": "#F6EAF8",

    "--mantine-color-teal-0": "#122422",
    "--mantine-color-teal-1": "#18302C",
    "--mantine-color-teal-2": "#204038",
    "--mantine-color-teal-3": "#2A5248",
    "--mantine-color-teal-4": "#4FA89A",
    "--mantine-color-teal-5": "#6BBAAE",
    "--mantine-color-teal-6": "#88CCC0",
    "--mantine-color-teal-7": "#A6DAD2",
    "--mantine-color-teal-8": "#C6E8E2",
    "--mantine-color-teal-9": "#E4F4F0",

    "--mantine-color-cyan-0": "#122428",
    "--mantine-color-cyan-1": "#183034",
    "--mantine-color-cyan-2": "#204044",
    "--mantine-color-cyan-3": "#2A5256",
    "--mantine-color-cyan-4": "#4AA8B8",
    "--mantine-color-cyan-5": "#68BAC8",
    "--mantine-color-cyan-6": "#86CCD6",
    "--mantine-color-cyan-7": "#A6DCE4",
    "--mantine-color-cyan-8": "#C6ECF0",
    "--mantine-color-cyan-9": "#E4F6F8",

    "--mantine-color-pink-0": "#2A141C",
    "--mantine-color-pink-1": "#3A1C28",
    "--mantine-color-pink-2": "#4A2634",
    "--mantine-color-pink-3": "#5E3244",
    "--mantine-color-pink-4": "#D07098",
    "--mantine-color-pink-5": "#DC88AC",
    "--mantine-color-pink-6": "#E6A0BE",
    "--mantine-color-pink-7": "#EEB8D0",
    "--mantine-color-pink-8": "#F4D0E0",
    "--mantine-color-pink-9": "#FAE8F0",

    "--mantine-color-violet-0": "#1E1830",
    "--mantine-color-violet-1": "#282040",
    "--mantine-color-violet-2": "#342A52",
    "--mantine-color-violet-3": "#443668",
    "--mantine-color-violet-4": "#9070D0",
    "--mantine-color-violet-5": "#A488DC",
    "--mantine-color-violet-6": "#B8A0E6",
    "--mantine-color-violet-7": "#CCB8EE",
    "--mantine-color-violet-8": "#E0D0F4",
    "--mantine-color-violet-9": "#F0E8FA",

    "--mantine-color-lime-0": "#1C2412",
    "--mantine-color-lime-1": "#263018",
    "--mantine-color-lime-2": "#324020",
    "--mantine-color-lime-3": "#40522A",
    "--mantine-color-lime-4": "#8AB84A",
    "--mantine-color-lime-5": "#A0C868",
    "--mantine-color-lime-6": "#B4D686",
    "--mantine-color-lime-7": "#C8E2A4",
    "--mantine-color-lime-8": "#DCEEC4",
    "--mantine-color-lime-9": "#F0F8E4",

    "--mantine-color-yellow-0": "#2A2410",
    "--mantine-color-yellow-1": "#3A3018",
    "--mantine-color-yellow-2": "#4A4020",
    "--mantine-color-yellow-3": "#5E522A",
    "--mantine-color-yellow-4": "#D4B04A",
    "--mantine-color-yellow-5": "#E0C268",
    "--mantine-color-yellow-6": "#E8D086",
    "--mantine-color-yellow-7": "#F0DEA4",
    "--mantine-color-yellow-8": "#F6EAC4",
    "--mantine-color-yellow-9": "#FBF4E4",

    // Mantine derives `*-light-color` / `*-outline` from shade 0 in dark mode
    // (default dark scales put bright text at 0). Our accent remaps put DEEP
    // surfaces at 0 so app code can keep `bg="lavender.0"` - which left light/
    // outline labels as near-invisible ink-on-ink ("Open bank", ThemeIcons…).
    // Pin those derived tokens to the bright text-on-ink shades instead.
    // Also lift `*-light` fills off pure ink so the chip/button silhouette reads.
    "--mantine-color-lavender-light": "#332B45",
    "--mantine-color-lavender-light-hover": "#433A5C",
    "--mantine-color-lavender-light-color": "#CDBFEA",
    "--mantine-color-lavender-outline": "#CDBFEA",
    "--mantine-color-lavender-outline-hover": "rgba(205, 191, 234, 0.12)",
    "--mantine-color-forest-light": "#234039",
    "--mantine-color-forest-light-hover": "#2E5148",
    "--mantine-color-forest-light-color": "#B3D1CB",
    "--mantine-color-forest-outline": "#B3D1CB",
    "--mantine-color-forest-outline-hover": "rgba(179, 209, 203, 0.12)",
    "--mantine-color-sage-light": "#2E4535",
    "--mantine-color-sage-light-hover": "#3A5843",
    "--mantine-color-sage-light-color": "#C5D8BE",
    "--mantine-color-sage-outline": "#C5D8BE",
    "--mantine-color-sage-outline-hover": "rgba(197, 216, 190, 0.12)",
    "--mantine-color-terracotta-light": "#4A2E22",
    "--mantine-color-terracotta-light-hover": "#5E3B2C",
    "--mantine-color-terracotta-light-color": "#F0C4B2",
    "--mantine-color-terracotta-outline": "#F0C4B2",
    "--mantine-color-terracotta-outline-hover": "rgba(240, 196, 178, 0.12)",
    "--mantine-color-green-light": "#2E4535",
    "--mantine-color-green-light-hover": "#3A5843",
    "--mantine-color-green-light-color": "#C5D8BE",
    "--mantine-color-green-outline": "#C5D8BE",
    "--mantine-color-green-outline-hover": "rgba(197, 216, 190, 0.12)",
    "--mantine-color-red-light": "#4A2E22",
    "--mantine-color-red-light-hover": "#5E3B2C",
    "--mantine-color-red-light-color": "#F0C4B2",
    "--mantine-color-red-outline": "#F0C4B2",
    "--mantine-color-red-outline-hover": "rgba(240, 196, 178, 0.12)",
    "--mantine-color-blue-light": "#332B45",
    "--mantine-color-blue-light-hover": "#433A5C",
    "--mantine-color-blue-light-color": "#CDBFEA",
    "--mantine-color-blue-outline": "#CDBFEA",
    "--mantine-color-blue-outline-hover": "rgba(205, 191, 234, 0.12)",
    "--mantine-color-orange-light": "#4A3220",
    "--mantine-color-orange-light-hover": "#5E412A",
    "--mantine-color-orange-light-color": "#F2CBA6",
    "--mantine-color-orange-outline": "#F2CBA6",
    "--mantine-color-orange-outline-hover": "rgba(242, 203, 166, 0.12)",
    "--mantine-color-indigo-light": "#2E3250",
    "--mantine-color-indigo-light-hover": "#3C4166",
    "--mantine-color-indigo-light-color": "#B8C0E8",
    "--mantine-color-indigo-outline": "#B8C0E8",
    "--mantine-color-indigo-outline-hover": "rgba(184, 192, 232, 0.12)",
    "--mantine-color-grape-light": "#3E2A44",
    "--mantine-color-grape-light-hover": "#503656",
    "--mantine-color-grape-light-color": "#DCBCE8",
    "--mantine-color-grape-outline": "#DCBCE8",
    "--mantine-color-grape-outline-hover": "rgba(220, 188, 232, 0.12)",
    "--mantine-color-teal-light": "#204038",
    "--mantine-color-teal-light-hover": "#2A5248",
    "--mantine-color-teal-light-color": "#A6DAD2",
    "--mantine-color-teal-outline": "#A6DAD2",
    "--mantine-color-teal-outline-hover": "rgba(166, 218, 210, 0.12)",
    "--mantine-color-cyan-light": "#204044",
    "--mantine-color-cyan-light-hover": "#2A5256",
    "--mantine-color-cyan-light-color": "#A6DCE4",
    "--mantine-color-cyan-outline": "#A6DCE4",
    "--mantine-color-cyan-outline-hover": "rgba(166, 220, 228, 0.12)",
    "--mantine-color-pink-light": "#4A2634",
    "--mantine-color-pink-light-hover": "#5E3244",
    "--mantine-color-pink-light-color": "#EEB8D0",
    "--mantine-color-pink-outline": "#EEB8D0",
    "--mantine-color-pink-outline-hover": "rgba(238, 184, 208, 0.12)",
    "--mantine-color-violet-light": "#342A52",
    "--mantine-color-violet-light-hover": "#443668",
    "--mantine-color-violet-light-color": "#CCB8EE",
    "--mantine-color-violet-outline": "#CCB8EE",
    "--mantine-color-violet-outline-hover": "rgba(204, 184, 238, 0.12)",
    "--mantine-color-lime-light": "#324020",
    "--mantine-color-lime-light-hover": "#40522A",
    "--mantine-color-lime-light-color": "#C8E2A4",
    "--mantine-color-lime-outline": "#C8E2A4",
    "--mantine-color-lime-outline-hover": "rgba(200, 226, 164, 0.12)",
    "--mantine-color-yellow-light": "#4A4020",
    "--mantine-color-yellow-light-hover": "#5E522A",
    "--mantine-color-yellow-light-color": "#F0DEA4",
    "--mantine-color-yellow-outline": "#F0DEA4",
    "--mantine-color-yellow-outline-hover": "rgba(240, 222, 164, 0.12)",
  },
});

const zivoColorSchemeManager = cookieColorSchemeManager();

function ColorSchemeCookieSync() {
  const pathname = usePathname();
  const { colorScheme } = useMantineColorScheme();
  useEffect(() => {
    if (isLightOnlyPath(pathname)) return;
    writeColorSchemeCookie(colorScheme === "dark" ? "dark" : "light");
  }, [colorScheme, pathname]);
  return null;
}

/** Keep variantColorResolver in sync when the user toggles theme (no document reads during render). */
function SsrColorSchemeSync() {
  const { colorScheme } = useMantineColorScheme();
  setSsrColorScheme(colorScheme === "dark" ? "dark" : "light");
  return null;
}

/**
 * One-time: users who only had localStorage (pre-cookie) get their preference
 * after hydration — never during the first paint (avoids SSR/client HTML drift).
 */
function LegacyColorSchemeMigration() {
  const { setColorScheme } = useMantineColorScheme();
  useEffect(() => {
    if (readColorSchemeCookieClient() !== null) return;
    try {
      const stored = window.localStorage.getItem(MANTINE_COLOR_SCHEME_STORAGE_KEY);
      if (stored === "dark" || stored === "light") {
        setColorScheme(stored);
      }
    } catch {
      /* ignore */
    }
  }, [setColorScheme]);
  return null;
}

/** Keep the root html attribute aligned when client-navigating to/from landing. */
function LandingSchemeSync() {
  const pathname = usePathname();
  const lightOnly = isLightOnlyPath(pathname);
  const { colorScheme } = useMantineColorScheme();

  useEffect(() => {
    if (lightOnly) {
      document.documentElement.setAttribute("data-mantine-color-scheme", "light");
      return;
    }
    document.documentElement.setAttribute(
      "data-mantine-color-scheme",
      colorScheme === "dark" ? "dark" : "light",
    );
  }, [lightOnly, colorScheme]);

  return null;
}

export default function Providers({
  children,
  colorScheme = "light",
}: {
  children: React.ReactNode;
  colorScheme?: MantineColorScheme;
}) {
  const pathname = usePathname();
  const lightOnly = isLightOnlyPath(pathname);
  setSsrColorScheme(lightOnly ? "light" : colorScheme);
  const [queryClient] = useState(createQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <MantineProvider
        theme={theme}
        cssVariablesResolver={cssVariablesResolver}
        defaultColorScheme={colorScheme}
        forceColorScheme={lightOnly ? "light" : undefined}
        colorSchemeManager={zivoColorSchemeManager}
      >
        <SsrColorSchemeSync />
        <LegacyColorSchemeMigration />
        <ColorSchemeCookieSync />
        <LandingSchemeSync />
        <Notifications position="top-right" />
        {children}
      </MantineProvider>
    </QueryClientProvider>
  );
}
