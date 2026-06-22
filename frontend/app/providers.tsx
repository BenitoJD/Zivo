"use client";

import { DEFAULT_THEME, MantineProvider, createTheme, mergeMantineTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";

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

const theme = mergeMantineTheme(
  DEFAULT_THEME,
  createTheme({
    fontFamily: "Inter, system-ui, sans-serif",
    headings: { fontFamily: "Inter, system-ui, sans-serif" },
    primaryColor: "gray",
    primaryShade: { light: 6, dark: 4 },
    autoContrast: true,
    defaultRadius: "md",
    colors: {
      dark: [...DARK_PALETTE],
    },
  }),
);

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <MantineProvider theme={theme} defaultColorScheme="dark">
      <Notifications position="top-right" />
      {children}
    </MantineProvider>
  );
}
