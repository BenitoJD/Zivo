"use client";

import { Box } from "@mantine/core";
import { useReducedMotion } from "framer-motion";

/**
 * Wispr-Flow-style animated mesh gradient. A few large, heavily-blurred pastel
 * blobs drift slowly behind the hero on the warm oat page, giving the soft
 * "aurora" first impression. Pure CSS keyframes (no JS rAF) so it's cheap and
 * GPU-composited. Falls back to a calm static wash under prefers-reduced-motion.
 *
 * Colors are pulled from the brand scales (forest teal + lavender) kept faint so
 * ink text and product mock stay perfectly legible on top.
 */
export function GradientBackdrop({
  intensity = 1,
}: {
  /** Multiplier on blob opacity — dial down for secondary sections. */
  intensity?: number;
}) {
  const reduce = useReducedMotion();
  const o = (v: number) => Math.min(1, v * intensity);

  return (
    <Box
      aria-hidden
      style={{
        position: "absolute",
        inset: 0,
        overflow: "hidden",
        pointerEvents: "none",
        zIndex: 0,
      }}
    >
      <Box
        className="zivo-blob zivo-blob-1"
        style={{
          background: `radial-gradient(circle at center, rgba(165, 193, 189, ${o(0.16)}), transparent 72%)`,
          animationPlayState: reduce ? "paused" : "running",
        }}
      />
      <Box
        className="zivo-blob zivo-blob-2"
        style={{
          background: `radial-gradient(circle at center, rgba(205, 187, 221, ${o(0.15)}), transparent 72%)`,
          animationPlayState: reduce ? "paused" : "running",
        }}
      />
      <Box
        className="zivo-blob zivo-blob-3"
        style={{
          background: `radial-gradient(circle at center, rgba(127, 163, 158, ${o(0.12)}), transparent 72%)`,
          animationPlayState: reduce ? "paused" : "running",
        }}
      />
      <Box
        className="zivo-blob zivo-blob-4"
        style={{
          background: `radial-gradient(circle at center, rgba(247, 222, 200, ${o(0.14)}), transparent 72%)`,
          animationPlayState: reduce ? "paused" : "running",
        }}
      />

      <style>{`
        .zivo-blob {
          position: absolute;
          border-radius: 50%;
          filter: blur(100px);
          will-change: transform;
          /* Multiply so overlapping pastels deepen into richer tints on the warm
             page instead of alpha-stacking into a blown-out white hotspot. Kept
             very faint so the page reads as near-uniform cream — no edge color shift. */
          mix-blend-mode: multiply;
        }
        .zivo-blob-1 {
          width: 46vw; height: 46vw; max-width: 620px; max-height: 620px;
          top: -12%; left: -6%;
          animation: zivo-drift-1 22s ease-in-out infinite alternate;
        }
        .zivo-blob-2 {
          width: 42vw; height: 42vw; max-width: 560px; max-height: 560px;
          top: -8%; right: -8%;
          animation: zivo-drift-2 26s ease-in-out infinite alternate;
        }
        .zivo-blob-3 {
          width: 40vw; height: 40vw; max-width: 520px; max-height: 520px;
          bottom: -18%; left: 18%;
          animation: zivo-drift-3 30s ease-in-out infinite alternate;
        }
        .zivo-blob-4 {
          width: 36vw; height: 36vw; max-width: 480px; max-height: 480px;
          bottom: -14%; right: 6%;
          animation: zivo-drift-4 24s ease-in-out infinite alternate;
        }
        @keyframes zivo-drift-1 {
          from { transform: translate3d(0, 0, 0) scale(1); }
          to   { transform: translate3d(8vw, 6vh, 0) scale(1.12); }
        }
        @keyframes zivo-drift-2 {
          from { transform: translate3d(0, 0, 0) scale(1.05); }
          to   { transform: translate3d(-7vw, 8vh, 0) scale(0.92); }
        }
        @keyframes zivo-drift-3 {
          from { transform: translate3d(0, 0, 0) scale(0.95); }
          to   { transform: translate3d(6vw, -7vh, 0) scale(1.15); }
        }
        @keyframes zivo-drift-4 {
          from { transform: translate3d(0, 0, 0) scale(1.1); }
          to   { transform: translate3d(-6vw, -5vh, 0) scale(0.9); }
        }
        @media (prefers-reduced-motion: reduce) {
          .zivo-blob { animation: none !important; }
        }
        /* In dark mode the pastels read as harsh glows — calm them right down.
           Screen blend keeps them as a soft additive glow without the white blowout. */
        [data-mantine-color-scheme="dark"] .zivo-blob {
          opacity: 0.4;
          filter: blur(90px);
          mix-blend-mode: screen;
        }
      `}</style>
    </Box>
  );
}
