"use client";

import { DEFAULT_THEME, MantineProvider, createTheme, mergeMantineTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";

/** OLED-style dark palette: body and surfaces use true #000. */
const DARK_BLACK = [
  "#E8E8E8",
  "#D4D4D4",
  "#A3A3A3",
  "#737373",
  "#333333",
  "#1A1A1A",
  "#000000",
  "#000000",
  "#000000",
  "#000000",
] as const;

const theme = mergeMantineTheme(
  DEFAULT_THEME,
  createTheme({
    fontFamily: "Inter, system-ui, sans-serif",
    headings: { fontFamily: "Inter, system-ui, sans-serif" },
    primaryColor: "gray",
    primaryShade: { light: 6, dark: 7 },
    autoContrast: true,
    defaultRadius: "md",
    black: "#000000",
    white: "#ffffff",
    colors: {
      dark: [...DARK_BLACK],
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
