"use client";

import { useEffect, useRef } from "react";
import { Box } from "@mantine/core";
import { useReducedMotion } from "framer-motion";
import { useMediaQuery } from "@mantine/hooks";

/**
 * The hero "generation" animation — Zivo turning a source into study questions.
 *
 * Visual story, left → right (desktop) / top → bottom (mobile):
 *   1. Faint, messy SOURCE text streams in on a curved path (the raw material).
 *   2. It collapses into a calm PROCESSING CORE — a glassy waveform pill ringed
 *      by soft concentric pulses, with cycling status badges narrating the work.
 *   3. Crisp generated QUESTIONS stream out on a deep-green ribbon (the result).
 *
 * Built as one SVG scene so text-on-path + the waveform stay crisp at any size.
 * Two geometry variants share one render path: a wide desktop scene and a
 * compact, reflowed mobile scene (narrower viewBox, gentler bleed) so it lives
 * on phones too — scaled and re-laid-out, never just shrunk.
 *
 * Motion is GPU-friendly (SVG attribute/transform animations, opacity) with
 * spring-ish `keySplines` easing for an intentional, Apple-calm feel. The whole
 * scene pauses when scrolled offscreen (cheap IntersectionObserver toggling
 * SVGSVGElement.pauseAnimations/unpauseAnimations) and degrades to a tasteful
 * static composition under prefers-reduced-motion.
 */

// Long looping marquee strings — built once at module load, not per render.
const SOURCE_SPEECH_TEXT = Array(36)
  .fill(
    "Umm, this biology PDF on cellular respiration... glycolysis yields 2 ATP... wait, where do carbon bonds break? what is the citric acid cycle... acetyl CoA... the electron transport chain makes NADH... I need this for the exam... it happens in the mitochondria... oxygen is the final electron acceptor... ",
  )
  .join("");
const QUESTION_STREAM_TEXT = Array(36)
  .fill(
    "Q: What is the final electron acceptor in the ETC?  ·  Q: Where does respiration take place?  ·  Q: Net ATP yield of glycolysis?  ·  Q: Which coenzymes form in the citric acid cycle?  ·  Q: What is the role of NADH?  ·  ",
  )
  .join("");

const PHRASES = [
  { t: "Reading your source", c: "#7B5DA6" },
  { t: "Mapping key concepts", c: "#034F46" },
  { t: "Spotting misconceptions", c: "#C57454" },
  { t: "Questions generated", c: "#034F46", check: true },
  { t: "Tuning difficulty", c: "#7B5DA6" },
  { t: "Ready to quiz", c: "#4B7A43", check: true },
] as const;

// Spring-ish ease (matches the brand cubic-bezier 0.32,0.72,0,1) for keySplines.
const SPRING = "0.32 0.72 0 1";

type Geometry = {
  /** viewBox of the scene. */
  vb: string;
  /** Aspect ratio (height / width) used to reserve box space without overflow. */
  ratio: number;
  /** Source text path. */
  sourceCurve: string;
  /** Question ribbon path. */
  questionCurve: string;
  /** Centre of the processing core. */
  core: { x: number; y: number };
  /** Where the status badge sits relative to the core centre. */
  badgeY: number;
  ribbonWidth: number;
  waveScale: number;
  sourceFont: number;
  questionFont: number;
};

// Wide desktop scene — a clean, mostly-horizontal flow with gentle curvature.
const DESKTOP: Geometry = {
  vb: "0 0 1000 320",
  ratio: 320 / 1000,
  // Source streams in from the upper-left, dipping toward the core.
  sourceCurve:
    "M -700 60 C -300 60, 60 110, 230 168 C 330 202, 400 196, 470 188",
  // Questions stream out from the core toward the upper-right.
  questionCurve:
    "M 560 178 C 700 172, 820 158, 980 128 C 1300 68, 1700 24, 2200 -40",
  core: { x: 512, y: 178 },
  badgeY: -58,
  ribbonWidth: 50,
  waveScale: 1,
  sourceFont: 15,
  questionFont: 16,
};

// Compact mobile scene — taller, with the flow folded so it reads top→bottom
// through the core. Narrower viewBox keeps everything inside the box (no bleed
// that would cause horizontal scroll on a ~360–430px phone).
const MOBILE: Geometry = {
  vb: "0 0 380 300",
  ratio: 300 / 380,
  // Source curls in from the top-left down into the core.
  sourceCurve: "M -260 30 C -60 40, 110 78, 170 116 C 196 132, 216 138, 236 142",
  // Questions sweep out to the lower-right.
  questionCurve:
    "M 244 158 C 300 168, 340 184, 420 210 C 560 256, 760 300, 1100 360",
  core: { x: 190, y: 150 },
  badgeY: -52,
  ribbonWidth: 42,
  waveScale: 0.82,
  sourceFont: 12.5,
  questionFont: 13,
};

const WAVE_BARS = [
  { x: 0, h: 4, dur: "1.4s" },
  { x: 5, h: 12, dur: "1.1s" },
  { x: 10, h: 24, dur: "0.9s" },
  { x: 15, h: 36, dur: "0.7s" },
  { x: 20, h: 16, dur: "1.0s" },
  { x: 25, h: 42, dur: "0.65s" },
  { x: 30, h: 8, dur: "1.2s" },
  { x: 35, h: 32, dur: "0.75s" },
  { x: 40, h: 6, dur: "1.35s" },
  { x: 45, h: 28, dur: "0.85s" },
  { x: 50, h: 40, dur: "0.6s" },
  { x: 55, h: 14, dur: "1.15s" },
  { x: 60, h: 34, dur: "0.7s" },
  { x: 65, h: 10, dur: "1.3s" },
  { x: 70, h: 26, dur: "0.8s" },
  { x: 75, h: 38, dur: "0.65s" },
  { x: 80, h: 18, dur: "1.25s" },
  { x: 85, h: 22, dur: "0.9s" },
  { x: 90, h: 36, dur: "0.7s" },
  { x: 95, h: 12, dur: "1.05s" },
];

export function GenerationAnimation() {
  const reduce = useReducedMotion();
  // 47.99em ≈ 768px — Mantine's `sm` boundary. Below this we use the compact,
  // reflowed scene instead of hiding the animation.
  const isMobile = useMediaQuery("(max-width: 47.99em)");
  // Avoid a hydration flash: only commit to a geometry once the media query has
  // resolved on the client. Default to desktop for SSR.
  const g: Geometry = isMobile ? MOBILE : DESKTOP;

  const svgRef = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Pause the whole SVG timeline when it scrolls offscreen — cheap and keeps a
  // mid laptop idle when the hero isn't in view.
  useEffect(() => {
    if (reduce) return;
    const svg = svgRef.current;
    const wrap = wrapRef.current;
    if (!svg || !wrap || typeof IntersectionObserver === "undefined") return;

    const io = new IntersectionObserver(
      ([entry]) => {
        try {
          if (entry.isIntersecting) svg.unpauseAnimations();
          else svg.pauseAnimations();
        } catch {
          /* unpause/pause not supported — harmless */
        }
      },
      { rootMargin: "120px" },
    );
    io.observe(wrap);
    return () => io.disconnect();
  }, [reduce, isMobile]);

  const N = PHRASES.length;
  const T = N * 2.1; // ~2.1s on screen per phrase

  return (
    <Box
      ref={wrapRef}
      aria-hidden
      style={{
        position: "relative",
        width: "100%",
        maxWidth: isMobile ? 440 : 980,
        margin: "0 auto",
        // Reserve height via aspect ratio so layout never thrashes as it loads.
        aspectRatio: isMobile ? "380 / 300" : "1000 / 320",
        // Critical for mobile: clip the decorative bleed so it can never push
        // horizontal scroll or overlap the hero copy.
        overflow: "hidden",
        // A whisper of mask at the edges so streaming text dissolves rather than
        // hard-clipping — the Apple "fade into the void" detail.
        WebkitMaskImage:
          "linear-gradient(to right, transparent 0, #000 7%, #000 93%, transparent 100%)",
        maskImage:
          "linear-gradient(to right, transparent 0, #000 7%, #000 93%, transparent 100%)",
      }}
    >
      <svg
        ref={svgRef}
        width="100%"
        height="100%"
        viewBox={g.vb}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        preserveAspectRatio="xMidYMid meet"
        style={{ display: "block", overflow: "visible" }}
      >
        <defs>
          {/* Soft green glow under the question ribbon — the "result" feels lit. */}
          <linearGradient id="zivoRibbon" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#034F46" />
            <stop offset="1" stopColor="#0E3B2E" />
          </linearGradient>
          <radialGradient id="zivoCoreGlow" cx="0.5" cy="0.5" r="0.5">
            <stop offset="0" stopColor="#7B5DA6" stopOpacity="0.18" />
            <stop offset="1" stopColor="#7B5DA6" stopOpacity="0" />
          </radialGradient>
          <filter id="zivoSoft" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="0.6" />
          </filter>

          <path id="zivoSourceCurve" d={g.sourceCurve} stroke="none" />
          <path
            id="zivoQuestionCurve"
            d={g.questionCurve}
            stroke="url(#zivoRibbon)"
            strokeWidth={g.ribbonWidth}
            strokeLinecap="round"
          />
        </defs>

        {/* Ambient glow behind the processing core. */}
        <circle
          cx={g.core.x}
          cy={g.core.y}
          r={isMobile ? 120 : 150}
          fill="url(#zivoCoreGlow)"
        />

        {/* The deep-green question ribbon (drawn first, text rides on top). */}
        <use href="#zivoQuestionCurve" />

        {/* Raw source speech — a faint whisper of messy input flowing in. */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: g.sourceFont,
            fontFamily: "var(--font-sans), 'Figtree', sans-serif",
            fill: "var(--mantine-color-text)",
            opacity: 0.4,
            letterSpacing: "0.03em",
          }}
        >
          <textPath href="#zivoSourceCurve" startOffset={reduce ? "-1400" : "-5400"}>
            {SOURCE_SPEECH_TEXT}
            {!reduce && (
              <animate
                attributeName="startOffset"
                from="-5400"
                to="0"
                dur="110s"
                repeatCount="indefinite"
              />
            )}
          </textPath>
        </text>

        {/* Polished questions — crisp white text on the ribbon. */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: g.questionFont,
            fontFamily: "var(--font-sans), sans-serif",
            fontWeight: 500,
            letterSpacing: "0.02em",
            fill: "#FFFFFF",
          }}
        >
          <textPath href="#zivoQuestionCurve" startOffset={reduce ? "-1400" : "-4800"}>
            {QUESTION_STREAM_TEXT}
            {!reduce && (
              <animate
                attributeName="startOffset"
                from="-4800"
                to="0"
                dur="78s"
                repeatCount="indefinite"
              />
            )}
          </textPath>
        </text>

        {/* ----- Processing core ----- */}
        <g transform={`translate(${g.core.x} ${g.core.y})`}>
          {/* Concentric pulse rings — calm, spring-eased breathing outward. */}
          {!reduce &&
            [0, 1, 2].map((i) => (
              <circle
                key={i}
                cx="0"
                cy="0"
                r={isMobile ? 38 : 46}
                fill="none"
                stroke="#7B5DA6"
                strokeWidth="1.4"
                opacity="0"
              >
                <animate
                  attributeName="r"
                  values={
                    isMobile ? "38;78;78" : "46;96;96"
                  }
                  keyTimes="0;0.7;1"
                  keySplines={`${SPRING};0 0 1 1`}
                  calcMode="spline"
                  dur="3.6s"
                  begin={`${i * 1.2}s`}
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="opacity"
                  values="0;0.32;0"
                  keyTimes="0;0.35;1"
                  dur="3.6s"
                  begin={`${i * 1.2}s`}
                  repeatCount="indefinite"
                />
              </circle>
            ))}

          {/* Cycling status badges — Zivo narrating its work, popping in/out. */}
          <g transform={`translate(0 ${g.badgeY})`}>
            {reduce ? (
              <StaticBadge phrase={PHRASES[3]} />
            ) : (
              PHRASES.map((p, i) => {
                const a = i / N;
                const b = (i + 1) / N;
                const f = 0.013;
                const kt = `0;${a.toFixed(3)};${(a + f).toFixed(3)};${(b - f).toFixed(
                  3,
                )};${b.toFixed(3)};1`;
                return (
                  <g key={i} opacity={0}>
                    <animate
                      attributeName="opacity"
                      values="0;0;1;1;0;0"
                      keyTimes={kt}
                      dur={`${T}s`}
                      repeatCount="indefinite"
                    />
                    <animateTransform
                      attributeName="transform"
                      type="scale"
                      values="0.8;0.8;1;1;0.97;0.97"
                      keyTimes={kt}
                      keySplines={`0 0 1 1;${SPRING};0 0 1 1;0 0 1 1;${SPRING}`}
                      calcMode="spline"
                      dur={`${T}s`}
                      repeatCount="indefinite"
                    />
                    <BadgePill phrase={p} />
                  </g>
                );
              })
            )}
          </g>

          {/* Glassy waveform pill. */}
          <g transform={`scale(${g.waveScale})`}>
            <rect
              x="-65"
              y="-28"
              width="130"
              height="56"
              rx="28"
              fill="var(--mantine-color-gray-0)"
              stroke="var(--mantine-color-default-border)"
              strokeWidth="1.5"
              style={{ filter: "drop-shadow(0 6px 18px rgba(20,19,16,0.12))" }}
            />
            {/* Fine inner highlight for a premium glass lip. */}
            <rect
              x="-62"
              y="-25"
              width="124"
              height="22"
              rx="11"
              fill="#FFFFFF"
              opacity="0.5"
              filter="url(#zivoSoft)"
            />
            <g transform="translate(-48 0)">
              {WAVE_BARS.map((bar, i) => (
                <rect
                  key={i}
                  x={bar.x}
                  y={-bar.h / 2}
                  width="2.4"
                  height={bar.h}
                  rx="1.2"
                  fill="var(--mantine-color-text)"
                  opacity="0.82"
                >
                  {!reduce && (
                    <>
                      <animate
                        attributeName="height"
                        values={`${bar.h * 0.35};${bar.h};${bar.h * 0.35}`}
                        keyTimes="0;0.5;1"
                        keySplines={`${SPRING};${SPRING}`}
                        calcMode="spline"
                        dur={bar.dur}
                        repeatCount="indefinite"
                      />
                      <animate
                        attributeName="y"
                        values={`${-bar.h * 0.175};${-bar.h / 2};${-bar.h * 0.175}`}
                        keyTimes="0;0.5;1"
                        keySplines={`${SPRING};${SPRING}`}
                        calcMode="spline"
                        dur={bar.dur}
                        repeatCount="indefinite"
                      />
                    </>
                  )}
                </rect>
              ))}
            </g>
          </g>
        </g>
      </svg>
    </Box>
  );
}

/** A single status pill (used both animated and static). */
function BadgePill({
  phrase,
}: {
  phrase: { t: string; c: string; check?: boolean };
}) {
  return (
    <>
      <rect
        x="-100"
        y="-17"
        width="200"
        height="34"
        rx="17"
        fill={phrase.c}
        style={{ filter: `drop-shadow(0 8px 20px ${phrase.c}4D)` }}
      />
      {phrase.check ? (
        <>
          <circle cx="-80" cy="0" r="8" fill="#FFFFFF" />
          <path
            d="M -83.5 0 L -81 2.6 L -76.5 -2.8"
            stroke={phrase.c}
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />
        </>
      ) : (
        <circle cx="-80" cy="0" r="5" fill="#FFFFFF">
          <animate
            attributeName="opacity"
            values="1;0.45;1"
            dur="1.1s"
            repeatCount="indefinite"
          />
        </circle>
      )}
      <text
        x="-64"
        y="1"
        dominantBaseline="central"
        style={{
          fontSize: 12.5,
          fontFamily: "var(--font-sans), sans-serif",
          fontWeight: 600,
          fill: "#FFFFFF",
          letterSpacing: "0.01em",
        }}
      >
        {phrase.t}
      </text>
    </>
  );
}

/** Reduced-motion: a calm, fully-resolved "Questions generated" badge. */
function StaticBadge({
  phrase,
}: {
  phrase: { t: string; c: string; check?: boolean };
}) {
  return (
    <g>
      <rect
        x="-100"
        y="-17"
        width="200"
        height="34"
        rx="17"
        fill={phrase.c}
        style={{ filter: `drop-shadow(0 8px 20px ${phrase.c}4D)` }}
      />
      <circle cx="-80" cy="0" r="8" fill="#FFFFFF" />
      <path
        d="M -83.5 0 L -81 2.6 L -76.5 -2.8"
        stroke={phrase.c}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <text
        x="-64"
        y="1"
        dominantBaseline="central"
        style={{
          fontSize: 12.5,
          fontFamily: "var(--font-sans), sans-serif",
          fontWeight: 600,
          fill: "#FFFFFF",
          letterSpacing: "0.01em",
        }}
      >
        {phrase.t}
      </text>
    </g>
  );
}
