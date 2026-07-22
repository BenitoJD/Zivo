"use client";

import { useEffect, useRef } from "react";
import { useLocalStorage } from "@mantine/hooks";
import { PetWorld, type PetWorldOptions } from "./engine";
import "./pets.css";

/** One preference gates every cat in the app (Learn, loading, generation waits…).
 *  Off by default; toggled in Settings. Kept here so PetPlayground self-enforces
 *  it - no usage can accidentally show a cat when the user turned them off. */
export const CAT_ENABLED_KEY = "zivo-cat-enabled";

/**
 * A little strip of pixel pets that walk, idle, jump and play while the user
 * waits. Click anywhere inside to toss a ball - the cats chase it. Honors
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
  wander = false,
  style,
  className,
}: {
  height?: number | string;
  className?: string;
  style?: React.CSSProperties;
} & PetWorldOptions) {
  const ref = useRef<HTMLDivElement>(null);
  const [enabled] = useLocalStorage({ key: CAT_ENABLED_KEY, defaultValue: false });

  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    const world = new PetWorld(el, { count, scale, species, interactive, sound, wander });
    return () => world.dispose();
  }, [enabled, count, scale, species, interactive, sound, wander]);

  // Cats off → render nothing at all (no empty box), everywhere in the app.
  if (!enabled) return null;

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
