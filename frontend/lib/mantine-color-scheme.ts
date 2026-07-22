export const MANTINE_COLOR_SCHEME_COOKIE = "mantine-color-scheme";

export type MantineColorScheme = "light" | "dark";

/** Server-safe: read the color scheme cookie set by the inline script / client sync. */
export function readColorSchemeFromCookie(value: string | undefined): MantineColorScheme {
  return value === "dark" ? "dark" : "light";
}

/**
 * Inline script for app/layout.tsx - runs before React hydrates.
 * Cookie + localStorage stay aligned so SSR and client agree on scheme.
 */
export const MANTINE_COLOR_SCHEME_SCRIPT = `try {
  function _readCookie(name) {
    var match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : null;
  }
  function _writeCookie(scheme) {
    document.cookie = "${MANTINE_COLOR_SCHEME_COOKIE}=" + scheme + ";path=/;max-age=31536000;SameSite=Lax";
  }
  if (window.location.pathname === "/") {
    document.documentElement.setAttribute("data-mantine-color-scheme", "light");
  } else {
    var fromCookie = _readCookie("${MANTINE_COLOR_SCHEME_COOKIE}");
    var fromStorage = window.localStorage.getItem("mantine-color-scheme-value");
    var preference = fromCookie || fromStorage || "light";
    // Light is the default for everyone - dark only when the user explicitly
    // picked it. "auto" (OS preference) is deliberately not honored.
    var colorScheme = preference === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-mantine-color-scheme", colorScheme);
    _writeCookie(colorScheme);
    if (!fromStorage || fromStorage !== colorScheme) {
      window.localStorage.setItem("mantine-color-scheme-value", colorScheme);
    }
  }
} catch (e) {}
`;

/** Public marketing routes that always render in light mode (workspace keeps theme toggle). */
export function isLightOnlyPath(pathname: string): boolean {
  return pathname === "/";
}

export function writeColorSchemeCookie(scheme: MantineColorScheme): void {
  if (typeof document === "undefined") return;
  document.cookie = `${MANTINE_COLOR_SCHEME_COOKIE}=${scheme};path=/;max-age=31536000;SameSite=Lax`;
}

let ssrColorScheme: MantineColorScheme = "light";

/** Called from Providers on each render so SSR matches the color-scheme cookie. */
export function setSsrColorScheme(scheme: MantineColorScheme): void {
  ssrColorScheme = scheme;
}

/** Scheme for theme resolvers - cookie/SSR on server, DOM attribute on client. */
export function resolveColorScheme(): MantineColorScheme {
  if (typeof document !== "undefined") {
    const scheme = document.documentElement.getAttribute("data-mantine-color-scheme");
    return scheme === "dark" ? "dark" : "light";
  }
  return ssrColorScheme;
}
