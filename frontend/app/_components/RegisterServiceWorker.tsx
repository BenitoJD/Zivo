"use client";

import { pick } from "@/lib/engineRuntime";
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
        return pick(Boolean(typeof window === "undefined"), () => {
            return;
        }, () => pick(Boolean(!("serviceWorker" in navigator)), () => {
            return;
        }, () => {
            // Register after load so it never competes with first-paint resources.
            const register = () => {
                navigator.serviceWorker.register("/sw.js").catch(() => {
                });
            };
            pick(Boolean(document.readyState === "complete"), () => {
                register();
            }, () => {
                window.addEventListener("load", register, { once: true });
            });
        }));
    }, []);
    return null;
}
