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
        height: "100%",
        minHeight: "400px",
        overflow: "hidden",
      }}
      h={{ base: 280, md: 400 }}
    >
      <svg
        width="100%"
        height="100%"
        viewBox="0 0 1000 400"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ display: "block", overflow: "visible" }}
      >
        {/* Path 1: Source text — an elegant, massive counter-clockwise loop-de-loop.
            Path extended infinitely to the top-left (-2000, -850) so it never starts on-screen. */}
        <path
          id="sourceCurve"
          d="M -2000 -850 L -200 50 C -100 100, 100 260, 250 260 C 400 260, 400 -20, 250 -20 C 100 -20, 100 260, 453 200"
          stroke="none"
        />

        {/* Path 2: Question ribbon — sleek dark band
            Path extended infinitely to the right (3000, -100) so it never ends on-screen. */}
        <path
          id="questionCurve"
          d="M 583 200 C 750 200, 850 190, 1000 160 C 1500 60, 2000 0, 3000 -100"
          stroke="var(--mantine-color-text)"
          strokeWidth="68"
          strokeLinecap="round"
        />

        {/* Source text — faint whisper, now bigger and more spaced out */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: 18,
            fontFamily: "var(--font-sans), 'Figtree', sans-serif",
            fill: "var(--mantine-color-text)",
            opacity: 0.45,
            letterSpacing: "0.05em",
          }}
        >
          <textPath href="#sourceCurve" startOffset="-6000">
            {Array(40).fill("Umm, I have this biology PDF on cellular respiration... glycolysis yields 2 ATP... wait, carbon bonds break... what is the citric acid cycle? Prep reaction produces acetyl CoA... Electron transport chain generates NADH... I need to memorize this for the exam... cellular respiration occurs in the mitochondria... oxygen is the final electron acceptor... ").join("")}
            <animate attributeName="startOffset" from="-6000" to="0" dur="120s" repeatCount="indefinite" />
          </textPath>
        </text>

        {/* Question text inside the dark ribbon — sleek sans-serif, bigger and bolder */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: 20,
            fontFamily: "var(--font-sans), sans-serif",
            fontWeight: 500,
            letterSpacing: "0.04em",
            fill: "var(--mantine-color-body)",
          }}
        >
          <textPath href="#questionCurve" startOffset="-6000">
            {Array(40).fill("Q: What is the final electron acceptor in the electron transport chain? · Q: Where does cellular respiration take place? · Q: What is the net yield of ATP from glycolysis? · Q: Which coenzymes are produced during the citric acid cycle? · Q: What is the role of NADH? · ").join("")}
            <animate attributeName="startOffset" from="-6000" to="0" dur="90s" repeatCount="indefinite" />
          </textPath>
        </text>

        {/* Floating badge above the pill — scaled up slightly and moved higher */}
        <g transform="translate(518, 110)">
          <rect
            x="-84"
            y="-18"
            width="168"
            height="36"
            rx="18"
            fill="var(--mantine-color-gray-0)"
            stroke="var(--mantine-color-text)"
            strokeOpacity="0.15"
            strokeWidth="1.5"
            style={{ filter: "drop-shadow(0 6px 16px rgba(0,0,0,0.08))" }}
          >
            <animate attributeName="y" values="-18;-21;-18" dur="3s" repeatCount="indefinite" />
          </rect>
          {/* Checkmark circle — dark green */}
          <circle cx="-60" cy="0" r="9" fill="#2D6A4F">
            <animate attributeName="cy" values="0;-3;0" dur="3s" repeatCount="indefinite" />
          </circle>
          <path d="M -64 0 L -61 3 L -55 -2" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none">
            <animate attributeName="d" values="M -64 0 L -61 3 L -55 -2;M -64 -3 L -61 0 L -55 -5;M -64 0 L -61 3 L -55 -2" dur="3s" repeatCount="indefinite" />
          </path>
          {/* Badge text — dark ink */}
          <text
            x="-44"
            y="1"
            dominantBaseline="central"
            style={{
              fontSize: 13,
              fontFamily: "var(--font-sans), sans-serif",
              fontWeight: 600,
              fill: "var(--mantine-color-text)",
            }}
          >
            Questions generated
            <animate attributeName="y" values="1;-2;1" dur="3s" repeatCount="indefinite" />
          </text>
        </g>

        {/* Central pill junction — large, prominent, with dense realistic waveform */}
        <g transform="translate(518, 200)">
          {/* Pill background — scaled up by 1.5x */}
          <rect
            x="-90"
            y="-40"
            width="180"
            height="80"
            rx="40"
            fill="var(--mantine-color-gray-0)"
            stroke="var(--mantine-color-text)"
            strokeWidth="2.5"
            style={{ filter: "drop-shadow(0 4px 16px rgba(0,0,0,0.08))" }}
          />
          {/* Dense waveform — scaled up proportionally */}
          <g transform="translate(-66, 0)">
            {[
              { x: 0,   h: 6,   dur: "1.4s" },
              { x: 7,   h: 18,  dur: "1.1s" },
              { x: 14,  h: 36,  dur: "0.9s" },
              { x: 21,  h: 54,  dur: "0.7s" },
              { x: 28,  h: 24,  dur: "1.0s" },
              { x: 35,  h: 63,  dur: "0.65s" },
              { x: 42,  h: 12,  dur: "1.2s" },
              { x: 49,  h: 45,  dur: "0.75s" },
              { x: 56,  h: 9,   dur: "1.35s" },
              { x: 63,  h: 42,  dur: "0.85s" },
              { x: 70,  h: 60,  dur: "0.6s" },
              { x: 77,  h: 21,  dur: "1.15s" },
              { x: 84,  h: 51,  dur: "0.7s" },
              { x: 91,  h: 15,  dur: "1.3s" },
              { x: 98,  h: 39,  dur: "0.8s" },
              { x: 105, h: 57,  dur: "0.65s" },
              { x: 112, h: 27,  dur: "1.25s" },
              { x: 119, h: 33,  dur: "0.9s" },
              { x: 126, h: 54,  dur: "0.7s" },
              { x: 133, h: 18,  dur: "1.05s" },
            ].map((bar, i) => (
              <rect
                key={i}
                x={bar.x}
                y={-bar.h / 2}
                width="3.5"
                height={bar.h}
                rx="1.75"
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
