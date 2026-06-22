/** Inline script for app/layout.tsx — mirrors @mantine/core ColorSchemeScript (server-safe). */
export const MANTINE_COLOR_SCHEME_SCRIPT = `try {
  var _colorScheme = window.localStorage.getItem("mantine-color-scheme-value");
  var colorScheme = _colorScheme === "light" || _colorScheme === "dark" || _colorScheme === "auto" ? _colorScheme : "dark";
  var computedColorScheme = colorScheme !== "auto" ? colorScheme : window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.setAttribute("data-mantine-color-scheme", computedColorScheme);
} catch (e) {}
`;

export const mantineHtmlProps = {
  suppressHydrationWarning: true,
  "data-mantine-color-scheme": "dark",
} as const;
