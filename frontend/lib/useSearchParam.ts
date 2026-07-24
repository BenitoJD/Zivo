"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Read one URL search param without next/navigation's `useSearchParams`.
 *
 * Next 16 (dev) instruments `useSearchParams` with a *conditional* `use()` on
 * `NavigationPromisesContext`. When that context flips null → object between
 * renders, React reports a Rules-of-Hooks violation in the caller
 * (WorkspaceArtifactPage: useContext vs useState at position 5).
 *
 * This hook always calls the same React hooks. Soft App Router navigations that
 * change only the query string are rare for `?mode=`; `popstate` covers back/forward.
 * We intentionally do **not** monkey-patch `history.pushState` (that races Next's
 * router init: "Router action dispatched before initialization").
 */

function subscribe(onChange: () => void) {
  if (typeof window === "undefined") return () => {};
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
}

function readParam(name: string): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(name);
}

/** Current value of `?name=` (null when absent). SSR snapshot is always null. */
export function useSearchParam(name: string): string | null {
  const getSnapshot = useCallback(() => readParam(name), [name]);
  return useSyncExternalStore(subscribe, getSnapshot, () => null);
}
