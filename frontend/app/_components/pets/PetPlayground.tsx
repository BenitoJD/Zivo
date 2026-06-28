"use client";

import { useEffect, useRef } from "react";
import { PetWorld, type PetWorldOptions } from "./engine";
import "./pets.css";

/**
 * A little strip of pixel pets that walk, idle, jump and play while the user
 * waits. Click anywhere inside to toss a ball — the cats chase it. Honors
 * prefers-reduced-motion (skips animation). Drop it into any loading surface.
 *
 * Self-cleaning: the PetWorld is torn down on unmount, so it's safe to mount
 * and discard as loading states come and go.
 */
export function PetPlayground({
  height = 120,
  count = 3,
  scale = 2,
  species = "random",
  interactive = true,
  sound = false,
  style,
  className,
}: {
  height?: number | string;
  className?: string;
  style?: React.CSSProperties;
} & PetWorldOptions) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const world = new PetWorld(el, { count, scale, species, interactive, sound });
    return () => world.dispose();
  }, [count, scale, species, interactive, sound]);

  return (
    <div
      ref={ref}
      className={`zv-pet-world${className ? ` ${className}` : ""}`}
      style={{
        position: "relative",
        width: "100%",
        height: typeof height === "number" ? `${height}px` : height,
        ...style,
      }}
      aria-hidden
    />
  );
}
