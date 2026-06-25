"use client";

import Link from "next/link";
import { Box, Button, Container, Group, Stack, Text } from "@mantine/core";
import {
  IconArrowRight,
  IconBrandApple,
  IconBrandWindows,
  IconDeviceMobile,
  IconBrandAndroid,
} from "@tabler/icons-react";
import { useReducedMotion } from "framer-motion";
import { GradientBackdrop } from "./GradientBackdrop";
import { ProductMock } from "./ProductMock";

/**
 * Wispr-Flow's signature hero motion, rebuilt as SVG. Raw, messy speech flows in
 * on a curved dashed path, collapses into a central waveform pill, and re-emerges
 * as polished exam questions on a solid dark ribbon. Gated on reduced-motion.
 */
function WavyTextAnimation() {
  const reduce = useReducedMotion();
  if (reduce) return null;

  return (
    <Box
      style={{
        position: "relative",
        width: "100%",
        maxWidth: 960,
        height: 340,
        margin: "0 auto",
        overflow: "visible",
      }}
    >
      <svg
        width="100%"
        height="100%"
        viewBox="0 0 1000 340"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ display: "block", overflow: "visible" }}
      >
        <path
          id="sourceCurve"
          d="M -2000 -850 L -200 50 C -100 100, 100 260, 250 260 C 400 260, 400 -20, 250 -20 C 100 -20, 100 260, 453 200"
          stroke="none"
        />
        <path
          id="questionCurve"
          d="M 583 200 C 750 200, 850 190, 1000 160 C 1500 60, 2000 0, 3000 -100"
          stroke="#0E3B2E"
          strokeWidth="54"
          strokeLinecap="round"
        />

        {/* Raw source speech — faint whisper flowing in */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: 15,
            fontFamily: "var(--font-sans), 'Figtree', sans-serif",
            fill: "var(--mantine-color-text)",
            opacity: 0.45,
            letterSpacing: "0.04em",
          }}
        >
          <textPath href="#sourceCurve" startOffset="-6000">
            {Array(40)
              .fill(
                "Umm, I have this biology PDF on cellular respiration... glycolysis yields 2 ATP... wait, carbon bonds break... what is the citric acid cycle? Prep reaction produces acetyl CoA... Electron transport chain generates NADH... I need to memorize this for the exam... cellular respiration occurs in the mitochondria... oxygen is the final electron acceptor... ",
              )
              .join("")}
            <animate attributeName="startOffset" from="-6000" to="0" dur="120s" repeatCount="indefinite" />
          </textPath>
        </text>

        {/* Polished questions on the lavender-green ribbon — white text (Wispr style) */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: 16,
            fontFamily: "var(--font-sans), sans-serif",
            fontWeight: 500,
            letterSpacing: "0.03em",
            fill: "#FFFFFF",
          }}
        >
          <textPath href="#questionCurve" startOffset="-6000">
            {Array(40)
              .fill(
                "Q: What is the final electron acceptor in the electron transport chain? · Q: Where does cellular respiration take place? · Q: What is the net yield of ATP from glycolysis? · Q: Which coenzymes are produced during the citric acid cycle? · Q: What is the role of NADH? · ",
              )
              .join("")}
            <animate attributeName="startOffset" from="-6000" to="0" dur="90s" repeatCount="indefinite" />
          </textPath>
        </text>

        {/* Floating "Questions generated" badge */}
        <g transform="translate(518, 145)">
          <rect
            x="-72"
            y="-16"
            width="144"
            height="32"
            rx="16"
            fill="#034F46"
            style={{ filter: "drop-shadow(0 4px 12px rgba(3,79,70,0.22))" }}
          >
            <animate attributeName="y" values="-16;-18;-16" dur="3s" repeatCount="indefinite" />
          </rect>
          <circle cx="-52" cy="0" r="8" fill="#FFFFFF">
            <animate attributeName="cy" values="0;-2;0" dur="3s" repeatCount="indefinite" />
          </circle>
          <path d="M -55.5 0 L -53 2.5 L -48.5 -2" stroke="#034F46" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" fill="none">
            <animate attributeName="d" values="M -55.5 0 L -53 2.5 L -48.5 -2;M -55.5 -2 L -53 0.5 L -48.5 -4;M -55.5 0 L -53 2.5 L -48.5 -2" dur="3s" repeatCount="indefinite" />
          </path>
          <text
            x="-38"
            y="1"
            dominantBaseline="central"
            style={{
              fontSize: 11,
              fontFamily: "var(--font-sans), sans-serif",
              fontWeight: 600,
              fill: "#FFFFFF",
            }}
          >
            Questions generated
            <animate attributeName="y" values="1;-1;1" dur="3s" repeatCount="indefinite" />
          </text>
        </g>

        {/* Central waveform pill */}
        <g transform="translate(518, 200)">
          <rect
            x="-65"
            y="-28"
            width="130"
            height="56"
            rx="28"
            fill="var(--mantine-color-gray-0)"
            stroke="var(--mantine-color-text)"
            strokeWidth="2"
            style={{ filter: "drop-shadow(0 3px 12px rgba(0,0,0,0.08))" }}
          />
          <g transform="translate(-48, 0)">
            {[
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
            ].map((bar, i) => (
              <rect
                key={i}
                x={bar.x}
                y={-bar.h / 2}
                width="2.5"
                height={bar.h}
                rx="1.25"
                fill="var(--mantine-color-text)"
                opacity="0.9"
              >
                <animate
                  attributeName="height"
                  values={`${bar.h * 0.35};${bar.h};${bar.h * 0.35}`}
                  dur={bar.dur}
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="y"
                  values={`${-bar.h * 0.175};${-bar.h / 2};${-bar.h * 0.175}`}
                  dur={bar.dur}
                  repeatCount="indefinite"
                />
              </rect>
            ))}
          </g>
        </g>
      </svg>
    </Box>
  );
}

const PLATFORMS = [
  { icon: IconBrandApple, label: "Mac" },
  { icon: IconBrandWindows, label: "Windows" },
  { icon: IconDeviceMobile, label: "iPhone" },
  { icon: IconBrandAndroid, label: "Android" },
];

/**
 * Wispr-Flow-style hero. A soft animated mesh-gradient sits behind a big, bold
 * sans headline (not serif — Wispr's signature), a lavender-green pill CTA, a
 * platform availability row, and the product mock as the hero visual.
 */
export function Hero() {
  return (
    <Box style={{ position: "relative", overflow: "hidden" }}>
      <GradientBackdrop />

      <Container
        size="lg"
        px={{ base: "md", md: "lg" }}
        py={{ base: 48, md: 80 }}
        style={{ position: "relative", zIndex: 1 }}
      >
        <Stack align="center" gap={32} style={{ textAlign: "center" }}>
          <Box
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 14px",
              borderRadius: 999,
              background: "color-mix(in srgb, var(--mantine-color-body) 78%, transparent)",
              backdropFilter: "blur(8px)",
              WebkitBackdropFilter: "blur(8px)",
              border: "1px solid var(--mantine-color-lavender-2)",
            }}
          >
            <Box
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: "var(--mantine-color-lavender-7)",
              }}
            />
            <Text
              size="xs"
              fw={600}
              tt="uppercase"
              lts={1.5}
              c="lavender.7"
              style={{ fontFamily: "var(--font-sans)" }}
            >
              Question Better.
            </Text>
          </Box>

          {/* Serif display headline — Wispr's two-weight treatment: a muted
              lighter first clause, then a bolder emphasised line. */}
          <Box
            component="h1"
            style={{
              fontWeight: 450,
              lineHeight: 1.06,
              fontSize: "clamp(2.7rem, 7vw, 5.25rem)",
              letterSpacing: "-0.02em",
              maxWidth: 980,
              margin: 0,
            }}
          >
            <Box component="span" style={{ color: "var(--mantine-color-gray-6)", fontWeight: 400 }}>
              Don&apos;t just read it.
            </Box>
            <br />
            <Box component="span" style={{ color: "var(--mantine-color-lavender-7)", fontWeight: 600 }}>
              Prove you know it.
            </Box>
          </Box>

          <Text
            size="xl"
            c="gray.6"
            maw={620}
            lh={1.55}
            style={{ margin: "0 auto", fontSize: "clamp(1.05rem, 2vw, 1.3rem)" }}
          >
            Upload anything and get exam-style questions scoped to exactly what you
            read — with a tutor that knows your source. Measure and improve
            understanding, one question at a time.
          </Text>

          <Stack gap={18} align="center">
            <Group gap="md" wrap="nowrap" justify="center">
              <Button
                size="lg"
                component={Link}
                href="/workspace"
                rightSection={<IconArrowRight size={18} stroke={2} />}
                className="hero-cta-primary"
              >
                Start studying — it&apos;s free
              </Button>
              <Button
                size="lg"
                variant="default"
                component={Link}
                href="/signup"
                className="hero-cta-secondary"
              >
                Create an account
              </Button>
            </Group>

            <Group gap={18} wrap="wrap" justify="center" mt={4}>
              {PLATFORMS.map((p) => (
                <Group key={p.label} gap={6} wrap="nowrap">
                  <p.icon size={16} stroke={1.6} color="var(--mantine-color-gray-6)" />
                  <Text
                    size="xs"
                    c="gray.6"
                    fw={500}
                    style={{ fontFamily: "var(--font-sans)", letterSpacing: "0.02em" }}
                  >
                    {p.label}
                  </Text>
                </Group>
              ))}
            </Group>
          </Stack>

          <WavyTextAnimation />

          <Box w="100%" maw={680} mt={8}>
            <ProductMock />
          </Box>
        </Stack>
      </Container>

      <style>{`
        /* Wispr "Download for macOS" — soft lavender pill (matches the nav CTA). */
        .hero-cta-primary {
          background-color: #E9DDF5 !important;
          color: var(--mantine-color-text) !important;
          border: 1px solid #D6C2EC !important;
          border-radius: 999px !important;
          font-family: var(--font-sans), sans-serif !important;
          font-weight: 600 !important;
          box-shadow: 0 6px 20px rgba(123, 93, 166, 0.18);
          transition: transform 150ms ease, box-shadow 200ms ease, background-color 200ms ease;
        }
        .hero-cta-primary:hover {
          background-color: #DFCDED !important;
          transform: translateY(-1px);
          box-shadow: 0 10px 28px rgba(123, 93, 166, 0.24);
        }
        .hero-cta-secondary {
          border-radius: 999px !important;
          font-family: var(--font-sans), sans-serif !important;
          font-weight: 600 !important;
          background: color-mix(in srgb, var(--mantine-color-body) 70%, transparent) !important;
          backdrop-filter: blur(8px);
          -webkit-backdrop-filter: blur(8px);
          transition: transform 150ms ease, border-color 200ms ease;
        }
        .hero-cta-secondary:hover {
          transform: translateY(-1px);
          border-color: var(--mantine-color-lavender-4) !important;
        }
      `}</style>
    </Box>
  );
}
