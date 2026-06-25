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
        height: 280,
        margin: "0 auto",
        overflow: "visible",
      }}
    >
      <svg
        width="100%"
        height="100%"
        viewBox="0 0 1000 280"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ display: "block", overflow: "visible" }}
      >
        {/* Path 1: Source text — vertical circular arc on the left.
            Text enters from far upper-left, sweeps downward in a compact arc,
            curves around the bottom, then rises back to enter the pill.
            Arc bottom stays within viewport height (y ≤ 280). */}
        <path
          id="sourceCurve"
          d="M -300 60 C -100 60, 30 60, 60 110 C 90 160, 50 260, 120 275 C 190 290, 300 250, 370 210 C 420 180, 450 172, 470 170"
          stroke="none"
        />

        {/* Path 2: Question ribbon — exits the pill almost perfectly flat,
            then very gradually, almost imperceptibly curves upward.
            The initial stretch (first 200px) is nearly horizontal. */}
        <path
          id="questionCurve"
          d="M 530 170 C 600 168, 720 160, 850 145 C 1000 128, 1250 105, 1600 90"
          stroke="var(--mantine-color-text)"
          strokeWidth="34"
          strokeLinecap="round"
        />

        {/* Source text — faint whisper of raw material flowing along the C-curve */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: 13,
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
            fontSize: 13,
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

        {/* Central pill junction — densely packed waveform bars like a real audio visualizer */}
        <g transform="translate(500, 170)">
          <rect
            x="-44"
            y="-22"
            width="88"
            height="44"
            rx="22"
            fill="var(--mantine-color-gray-0)"
            stroke="var(--mantine-color-text)"
            strokeWidth="1"
            style={{ filter: "drop-shadow(0 2px 8px rgba(0,0,0,0.05))" }}
          />
          {/* Dense waveform bars — 16 thin bars packed tightly */}
          <g transform="translate(-30, 0)">
            {[
              { x: 0,  h: 14, dur: "1.2s" },
              { x: 4,  h: 22, dur: "0.8s" },
              { x: 8,  h: 10, dur: "1.4s" },
              { x: 12, h: 18, dur: "1.0s" },
              { x: 16, h: 26, dur: "0.7s" },
              { x: 20, h: 12, dur: "1.3s" },
              { x: 24, h: 20, dur: "0.9s" },
              { x: 28, h: 16, dur: "1.1s" },
              { x: 32, h: 24, dur: "0.75s" },
              { x: 36, h: 10, dur: "1.35s" },
              { x: 40, h: 18, dur: "0.85s" },
              { x: 44, h: 14, dur: "1.15s" },
              { x: 48, h: 22, dur: "0.95s" },
              { x: 52, h: 8,  dur: "1.25s" },
              { x: 56, h: 16, dur: "0.65s" },
            ].map((bar, i) => (
              <rect
                key={i}
                x={bar.x}
                y={-bar.h / 2}
                width="1.8"
                height={bar.h}
                rx="0.9"
                fill="var(--mantine-color-text)"
                opacity="0.6"
              >
                <animate
                  attributeName="height"
                  values={`${bar.h * 0.4};${bar.h};${bar.h * 0.4}`}
                  dur={bar.dur}
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="y"
                  values={`${-bar.h * 0.2};${-bar.h / 2};${-bar.h * 0.2}`}
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
