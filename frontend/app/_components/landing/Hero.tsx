"use client";

import Link from "next/link";
import { Box, Button, Container, Group, Stack, Text, Title } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { useReducedMotion } from "framer-motion";
import { ProductMock } from "./ProductMock";

/**
 * Custom SVG Wavy Text Marquee.
 * Animates raw study materials on a dashed path flowing into a central Zivo node,
 * and structured exam questions emerging on a solid black ribbon.
 */
function WavyTextAnimation() {
  const reduce = useReducedMotion();

  if (reduce) {
    return null;
  }

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
        {/* Path 1: Source text — vertical arc from upper-left, sweeping down and into the pill */}
        <path
          id="sourceCurve"
          d="M -300 60 C -100 60, 30 60, 60 120 C 90 180, 50 300, 130 320 C 210 340, 340 280, 410 230 C 450 205, 460 200, 475 200"
          stroke="none"
        />

        {/* Path 2: Question ribbon — thick dark band, nearly horizontal exit with gentle upward sweep */}
        <path
          id="questionCurve"
          d="M 560 200 C 650 198, 770 185, 900 165 C 1050 142, 1300 110, 1600 90"
          stroke="var(--mantine-color-text)"
          strokeWidth="40"
          strokeLinecap="round"
        />

        {/* Source text — faint whisper of raw material */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: 14,
            fontFamily: "var(--font-sans), 'Figtree', sans-serif",
            fill: "var(--mantine-color-text)",
            opacity: 0.32,
          }}
        >
          <textPath href="#sourceCurve" startOffset="-3000">
            {Array(20).fill("Umm, I have this biology PDF on cellular respiration... glycolysis yields 2 ATP... wait, carbon bonds break... what is the citric acid cycle? Prep reaction produces acetyl CoA... Electron transport chain generates NADH... I need to memorize this for the exam... cellular respiration occurs in the mitochondria... oxygen is the final electron acceptor... ").join("")}
            <animate attributeName="startOffset" from="-3000" to="0" dur="60s" repeatCount="indefinite" />
          </textPath>
        </text>

        {/* Question text inside the dark ribbon — cream/body-colored, serif */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: 15,
            fontFamily: "var(--font-serif), 'EB Garamond', Georgia, serif",
            fontWeight: 600,
            fill: "var(--mantine-color-body)",
          }}
        >
          <textPath href="#questionCurve" startOffset="-3000">
            {Array(20).fill("Q: What is the final electron acceptor in the electron transport chain? · Q: Where does cellular respiration take place? · Q: What is the net yield of ATP from glycolysis? · Q: Which coenzymes are produced during the citric acid cycle? · Q: What is the role of NADH? · ").join("")}
            <animate attributeName="startOffset" from="-3000" to="0" dur="45s" repeatCount="indefinite" />
          </textPath>
        </text>

        {/* Floating badge above the pill — cream background, dark green check, dark text */}
        <g transform="translate(518, 145)">
          <rect
            x="-72"
            y="-16"
            width="144"
            height="32"
            rx="16"
            fill="var(--mantine-color-gray-0)"
            stroke="var(--mantine-color-dark-1)"
            strokeWidth="0.75"
            style={{ filter: "drop-shadow(0 2px 8px rgba(0,0,0,0.06))" }}
          >
            <animate attributeName="y" values="-16;-18;-16" dur="3s" repeatCount="indefinite" />
          </rect>
          {/* Checkmark circle — dark green */}
          <circle cx="-52" cy="0" r="8" fill="#2D6A4F">
            <animate attributeName="cy" values="0;-2;0" dur="3s" repeatCount="indefinite" />
          </circle>
          <path d="M -55.5 0 L -53 2.5 L -48.5 -2" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" fill="none">
            <animate attributeName="d" values="M -55.5 0 L -53 2.5 L -48.5 -2;M -55.5 -2 L -53 0.5 L -48.5 -4;M -55.5 0 L -53 2.5 L -48.5 -2" dur="3s" repeatCount="indefinite" />
          </path>
          {/* Badge text — dark ink */}
          <text
            x="-38"
            y="1"
            dominantBaseline="central"
            style={{
              fontSize: 11,
              fontFamily: "var(--font-sans), sans-serif",
              fontWeight: 600,
              fill: "var(--mantine-color-text)",
            }}
          >
            Questions generated
            <animate attributeName="y" values="1;-1;1" dur="3s" repeatCount="indefinite" />
          </text>
        </g>

        {/* Central pill junction — large, prominent, with dense realistic waveform */}
        <g transform="translate(518, 200)">
          {/* Pill background — larger, thicker border, subtle shadow */}
          <rect
            x="-65"
            y="-28"
            width="130"
            height="56"
            rx="28"
            fill="var(--mantine-color-gray-0)"
            stroke="var(--mantine-color-text)"
            strokeWidth="1.5"
            style={{ filter: "drop-shadow(0 3px 12px rgba(0,0,0,0.08))" }}
          />
          {/* Dense waveform — 25 bars with dots interspersed, mimicking a real audio waveform.
              Heights vary dramatically: some are just dots (2-3px), others reach near-max (36px).
              This creates the authentic "audio waveform" silhouette from the Wispr Flow close-up. */}
          <g transform="translate(-48, 0)">
            {[
              { x: 0,   h: 4,  dur: "1.4s" },
              { x: 4,   h: 8,  dur: "1.1s" },
              { x: 8,   h: 16, dur: "0.9s" },
              { x: 12,  h: 28, dur: "0.7s" },
              { x: 16,  h: 18, dur: "1.0s" },
              { x: 20,  h: 36, dur: "0.65s" },
              { x: 24,  h: 12, dur: "1.2s" },
              { x: 28,  h: 30, dur: "0.75s" },
              { x: 32,  h: 6,  dur: "1.35s" },
              { x: 36,  h: 22, dur: "0.85s" },
              { x: 40,  h: 38, dur: "0.6s" },
              { x: 44,  h: 14, dur: "1.15s" },
              { x: 48,  h: 32, dur: "0.7s" },
              { x: 52,  h: 8,  dur: "1.3s" },
              { x: 56,  h: 26, dur: "0.8s" },
              { x: 60,  h: 34, dur: "0.65s" },
              { x: 64,  h: 10, dur: "1.25s" },
              { x: 68,  h: 20, dur: "0.9s" },
              { x: 72,  h: 36, dur: "0.7s" },
              { x: 76,  h: 16, dur: "1.05s" },
              { x: 80,  h: 28, dur: "0.75s" },
              { x: 84,  h: 6,  dur: "1.4s" },
              { x: 88,  h: 14, dur: "1.1s" },
              { x: 92,  h: 4,  dur: "1.3s" },
            ].map((bar, i) => (
              <rect
                key={i}
                x={bar.x}
                y={-bar.h / 2}
                width="2"
                height={bar.h}
                rx="1"
                fill="var(--mantine-color-text)"
                opacity="0.7"
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

export function Hero() {
  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 64 }}>
      <Stack align="center" gap={36} style={{ textAlign: "center" }}>
        <Box
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "6px 14px",
            borderRadius: "var(--mantine-radius-xl)",
            background: "var(--mantine-color-lavender-0)",
            border: "1px solid var(--mantine-color-lavender-2)",
          }}
        >
          <Text
            size="xs"
            fw={600}
            tt="uppercase"
            lts={1.5}
            c="lavender.8"
            style={{ fontFamily: "var(--font-sans)" }}
          >
            Question Better.
          </Text>
        </Box>

        <Title
          order={1}
          className="serif-text"
          style={{
            fontWeight: 500,
            lineHeight: 1.1,
            fontSize: "clamp(2.4rem, 6vw, 4.5rem)",
            letterSpacing: "-0.025em",
            maxWidth: 900,
            margin: "0 auto",
          }}
        >
          <Box
            component="span"
            style={{
              color: "var(--mantine-color-text)",
              opacity: 0.35,
              fontWeight: 400,
            }}
          >
            Turn what you read into
          </Box>{" "}
          <Box
            component="span"
            fs="italic"
            c="lavender.7"
            style={{ fontWeight: 500 }}
          >
            questions
          </Box>{" "}
          <Box component="span" style={{ color: "var(--mantine-color-text)", fontWeight: 500 }}>
            that actually test it.
          </Box>
        </Title>

        <Text size="lg" c="gray.6" maw={600} lh={1.6} style={{ margin: "0 auto" }}>
          Upload anything. Get exam-style questions, scoped exactly to what you
          read, with a tutor that knows your source. Measure and improve
          understanding — one question at a time.
        </Text>

        <Stack gap={14} align="center">
          <Group gap="md" wrap="nowrap" justify="center">
            <Button
              size="lg"
              component={Link}
              href="/workspace"
              rightSection={<IconArrowRight size={18} stroke={1.75} />}
              className="nav-cta-button"
              style={{
                transition: "transform 150ms ease, box-shadow 150ms ease",
              }}
            >
              Start studying
            </Button>
            <Button
              size="lg"
              variant="subtle"
              color="gray"
              component={Link}
              href="/signup"
              style={{
                color: "var(--mantine-color-text)",
                fontWeight: 500,
              }}
            >
              Create an account
            </Button>
          </Group>
          <Group gap={6} wrap="nowrap" justify="center">
            <Text size="xs" c="gray.5" fw={500} style={{ fontFamily: "var(--font-sans)", letterSpacing: "0.02em" }}>
              Available on Mac, Windows, iPhone, and Android
            </Text>
          </Group>
        </Stack>

        <WavyTextAnimation />

        <Box w="100%" maw={640}>
          <ProductMock />
        </Box>
      </Stack>

      <style>{`
        .nav-cta-button {
          background-color: #E9DDF5 !important;
          color: var(--mantine-color-text) !important;
          border: 1.5px solid var(--mantine-color-text) !important;
          border-radius: 12px !important;
          font-family: var(--font-sans), sans-serif !important;
          font-weight: 600 !important;
        }
        .nav-cta-button:hover {
          background-color: #DFCDED !important;
          transform: translateY(-1px);
        }
      `}</style>
    </Container>
  );
}
