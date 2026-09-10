"use client";

import { useEffect, useLayoutEffect, useRef } from "react";
import { pick } from "@/lib/engineRuntime";
import DesignLabApp from "@/vendor/system-design-lab/src/App";

/**
 * Mount for the Design Lab engine (frontend/vendor/system-design-lab).
 *
 * The wrapper div is the engine's page root: scoped engine CSS keys off
 * `.bscope` (vendor/system-design-lab/VENDOR.md), and `.app` fills this
 * wrapper rather than the viewport.
 *
 * Theming is Zivo's, not the lab's. Zivo owns one switch — the sidebar's
 * dark/light toggle, surfaced as `data-mantine-color-scheme` on <html> — and
 * this mount projects it onto the attribute the engine's stylesheet reads
 * (`data-theme`), so the whole app changes skin together. "light" is set
 * explicitly (not just cleared) so the engine's own prefers-color-scheme
 * fallback can never disagree with Zivo while the lab is open. Unmount
 * releases the attribute again.
 */
export default function DesignLab() {
    const scopeRef = useRef<HTMLDivElement | null>(null);

    const apply = () => {
        const html = document.documentElement;
        const scope = scopeRef.current;
        pick(scope === null, () => undefined, () => {
            const dark = html.getAttribute("data-mantine-color-scheme") === "dark";
            html.setAttribute("data-theme", pick(dark, () => "dark", () => "light"));
            scope!.setAttribute("data-theme", pick(dark, () => "dark", () => "light"));
        });
    };

    // Paint-synchronous: the engine's first frame is already in Zivo's skin.
    useLayoutEffect(apply, []);

    useEffect(() => {
        const observer = new MutationObserver(apply);
        observer.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ["data-mantine-color-scheme"],
        });
        return () => {
            observer.disconnect();
            document.documentElement.removeAttribute("data-theme");
        };
    }, []);

    return (<div ref={scopeRef} className="bscope" style={{ width: "100%", height: "100%" }}>
      <DesignLabApp/>
    </div>);
}
