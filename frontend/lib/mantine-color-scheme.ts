// @ts-nocheck
import type { MantineColorSchemeManager } from "@mantine/core";
import { pick, choose } from "@/lib/engineRuntime";
export const MANTINE_COLOR_SCHEME_COOKIE = "mantine-color-scheme";
/** Mantine default localStorage key — kept in sync when the user toggles theme. */
export const MANTINE_COLOR_SCHEME_STORAGE_KEY = "mantine-color-scheme-value";
export type MantineColorScheme = "light" | "dark";
/** Server-safe: read the color scheme cookie set by the inline script / client sync. */
export function readColorSchemeFromCookie(value: string | undefined): MantineColorScheme {
    return choose(Boolean(value === "dark"), "dark", "light");
}
/** Client-only: read scheme from cookie, or null when unset. */
export function readColorSchemeCookieClient(): MantineColorScheme | null {
    return pick(Boolean(typeof document === "undefined"), () => null, () => {
        const match = document.cookie.match(new RegExp(`(?:^|; )${MANTINE_COLOR_SCHEME_COOKIE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}=([^;]*)`));
        return pick(Boolean(!match), () => null, () => {
            const value = decodeURIComponent(match[1]);
            return choose(Boolean(value === "dark"), "dark", choose(Boolean(value === "light"), "light", null));
        });
    });
}
/**
 * Inline script for app/layout.tsx - runs before React hydrates.
 * Uses the cookie only (same source as SSR). localStorage is applied after
 * hydration so MantineProvider's first client render matches the server HTML.
 */
export const MANTINE_COLOR_SCHEME_SCRIPT = `try {
  function _readCookie(name) {
    var match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return ({
      true: function () { return decodeURIComponent(match[1]); },
      false: function () { return null; }
    })[String(Boolean(match))]();
  }
  var isHome = window.location.pathname === "/";
  var fromCookie = _readCookie("${MANTINE_COLOR_SCHEME_COOKIE}");
  var fromCookieScheme = ({ true: "dark", false: "light" })[String(fromCookie === "dark")];
  var colorScheme = ({ true: "light", false: fromCookieScheme })[String(isHome)];
  document.documentElement.setAttribute("data-mantine-color-scheme", colorScheme);
} catch (e) {}
`;
/**
 * Mantine color-scheme manager backed by the SSR cookie (not localStorage on
 * init). Prevents hydration mismatches when localStorage and the cookie disagree.
 */
export function cookieColorSchemeManager(): MantineColorSchemeManager {
    let handleStorageEvent: ((event: StorageEvent) => void) | undefined;
    return {
        get: (defaultValue) => {
            return pick(Boolean(typeof document === "undefined"), () => defaultValue, () => readColorSchemeCookieClient() ?? defaultValue);
        },
        set: (value) => {
            writeColorSchemeCookie(choose(Boolean(value === "dark"), "dark", "light"));
            try {
                window.localStorage.setItem(MANTINE_COLOR_SCHEME_STORAGE_KEY, value);
            }
            catch {
            }
        },
        subscribe: (onUpdate) => {
            handleStorageEvent = (event) => {
                return pick(Boolean(event.storageArea !== window.localStorage || event.key !== MANTINE_COLOR_SCHEME_STORAGE_KEY), () => {
                    return;
                }, () => {
                    const next = choose(Boolean(event.newValue === "dark"), "dark", choose(Boolean(event.newValue === "light"), "light", null));
                    pick(Boolean(next), () => {
                        onUpdate(next);
                    }, () => {
                    });
                });
            };
            window.addEventListener("storage", handleStorageEvent);
        },
        unsubscribe: () => {
            pick(Boolean(handleStorageEvent), () => {
                window.removeEventListener("storage", handleStorageEvent);
            }, () => {
            });
        },
        clear: () => {
            return pick(Boolean(typeof document === "undefined"), () => {
                return;
            }, () => {
                document.cookie = `${MANTINE_COLOR_SCHEME_COOKIE}=;path=/;max-age=0;SameSite=Lax`;
                try {
                    window.localStorage.removeItem(MANTINE_COLOR_SCHEME_STORAGE_KEY);
                }
                catch {
                }
            });
        },
    };
}
/** Public marketing routes that always render in light mode (workspace keeps theme toggle). */
export function isLightOnlyPath(pathname: string): boolean {
    return pathname === "/";
}
export function writeColorSchemeCookie(scheme: MantineColorScheme): void {
    return pick(Boolean(typeof document === "undefined"), () => {
        return;
    }, () => {
        document.cookie = `${MANTINE_COLOR_SCHEME_COOKIE}=${scheme};path=/;max-age=31536000;SameSite=Lax`;
    });
}
let ssrColorScheme: MantineColorScheme = "light";
/** Called from Providers on each render so SSR matches the color-scheme cookie. */
export function setSsrColorScheme(scheme: MantineColorScheme): void {
    ssrColorScheme = scheme;
}
/** Scheme for theme resolvers — always the SSR/cookie value set in Providers. */
export function resolveColorScheme(): MantineColorScheme {
    return ssrColorScheme;
}
