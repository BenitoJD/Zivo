"use client";

import { useEffect } from "react";

/**
 * Registers the Offline Mode service worker (ADR 0006).
 *
 * Replaces the old `unregister-stale-sw` inline script that actively tore down
 * any service worker on load. The SW only caches the app shell so a reload with
 * no signal doesn't white-screen; offline content lives in IndexedDB (Dexie).
 */
export default function RegisterServiceWorker() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;
    // Register after load so it never competes with first-paint resources.
    const register = () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        // Registration is best-effort; offline reload support degrades silently.
      });
    };
    if (document.readyState === "complete") register();
    else window.addEventListener("load", register, { once: true });
  }, []);
  return null;
}
