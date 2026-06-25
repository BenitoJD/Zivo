"use client";

import Link from "next/link";
import { Box, Button, Container, Group, Stack, Text, Title } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { motion, useReducedMotion } from "framer-motion";
import { EASE } from "./motion";
import { ProductMock } from "./ProductMock";
import { BrandMark } from "@/app/_components/BrandMark";

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
        height: 180,
        margin: "0 auto",
        overflow: "hidden",
        WebkitMaskImage: "linear-gradient(to right, transparent, #000 15%, #000 85%, transparent)",
        maskImage: "linear-gradient(to right, transparent, #000 15%, #000 85%, transparent)",
      }}
    >
      <svg
        width="100%"
        height="100%"
        viewBox="0 0 900 180"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ display: "block" }}
      >
        {/* Path 1: Source Raw Material (Input) - Clean, faded dashed line */}
        <path
          id="sourceCurve"
          d="M -20 120 C 150 120, 250 40, 450 90"
          stroke="var(--mantine-color-default-border)"
          strokeWidth="1.5"
          strokeDasharray="4 4"
        />

        {/* Path 2: Questions (Output) - Thick, solid rounded black/ink ribbon */}
        <path
          id="questionCurve"
          d="M 450 90 C 650 140, 750 40, 920 40"
          stroke="var(--mantine-color-text)"
          strokeWidth="32"
          strokeLinecap="round"
        />

        {/* Text along Path 1 (Source Input) */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: 12,
            fontFamily: "var(--font-sans), 'Figtree', sans-serif",
            fill: "var(--mantine-color-text)",
            opacity: 0.35,
          }}
        >
          <textPath href="#sourceCurve" startOffset="-1000">
            Umm, I have this biology PDF on cellular respiration... glycolysis yields 2 ATP... wait, carbon bonds break... what is the citric acid cycle? Prep reaction produces acetyl CoA... Electron transport chain generates NADH... I need to memorize this for the exam... cellular respiration occurs in the mitochondria... oxygen is the final electron acceptor...
            <animate attributeName="startOffset" from="-1000" to="0" dur="30s" repeatCount="indefinite" />
          </textPath>
        </text>

        {/* Text along Path 2 (Question Output inside Ribbon) - Bold cream text centered in the black band */}
        <text
          dominantBaseline="central"
          style={{
            fontSize: 13,
            fontFamily: "var(--font-serif), 'EB Garamond', Georgia, serif",
            fontWeight: 600,
            fill: "var(--mantine-color-body)",
          }}
        >
          <textPath href="#questionCurve" startOffset="-1000">
            Q: What is the final electron acceptor in the electron transport chain? · Q: Where does cellular respiration take place? · Q: What is the net yield of ATP from glycolysis? · Q: Which coenzymes are produced during the citric acid cycle? · Q: What is the role of NADH?
            <animate attributeName="startOffset" from="-1000" to="0" dur="26s" repeatCount="indefinite" />
          </textPath>
        </text>

        {/* Tactile Central Junction Node: Pill-shaped badge containing a sharp vector Z logo */}
        <g transform="translate(450, 90)">
          <rect
            x="-32"
            y="-16"
            width="64"
            height="32"
            rx="16"
            fill="var(--mantine-color-gray-0)"
            stroke="var(--mantine-color-text)"
            strokeWidth="1.5"
            style={{ filter: "drop-shadow(0 4px 10px rgba(0,0,0,0.06))" }}
          />
          {/* A razor-sharp vector SVG icon of a stylized "Z" page flow and question loop */}
          <g transform="translate(-1, -1)">
            <path
              d="M -6 -5 L 6 -5 L -6 5 L 6 5"
              stroke="var(--mantine-color-lavender-7)"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle cx="6" cy="-5" r="1.5" fill="var(--mantine-color-lavender-7)" />
          </g>
        </g>
      </svg>
    </Box>
  );
}

/**
 * Editorial centered hero: centered serif headline, forest-tinted key phrase;
 * dynamic SVG textPath curves, and the auto-advancing ProductMock question card centered below.
 */
export function Hero() {
  const reduce = useReducedMotion();
  const enter = reduce
    ? { initial: false as const, animate: undefined }
    : {
        initial: { opacity: 0, y: 16 },
        animate: { opacity: 1, y: 0, transition: { duration: 0.6, ease: EASE } },
      };

  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 64 }}>
      <Stack align="center" gap={36} style={{ textAlign: "center" }}>
        <motion.div {...enter}>
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
        </motion.div>

        <motion.div
          {...(reduce
            ? { initial: false as const, animate: undefined }
            : {
                initial: { opacity: 0, y: 18 },
                animate: {
                  opacity: 1,
                  y: 0,
                  transition: { duration: 0.7, ease: EASE, delay: 0.06 },
                },
              })}
        >
          <Title
            order={1}
            style={{
              fontFamily: "var(--font-serif), Georgia, serif",
              fontWeight: 500,
              lineHeight: 1.1,
              fontSize: "clamp(2.4rem, 6vw, 4.5rem)",
              letterSpacing: "-0.025em",
              maxWidth: 850,
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
              style={{ fontFamily: "var(--font-serif), Georgia, serif", fontWeight: 500 }}
            >
              questions
            </Box>{" "}
            <Box component="span" style={{ color: "var(--mantine-color-text)", fontWeight: 500 }}>
              that actually test it.
            </Box>
          </Title>
        </motion.div>

        <motion.div
          {...(reduce
            ? { initial: false as const, animate: undefined }
            : {
                initial: { opacity: 0, y: 18 },
                animate: {
                  opacity: 1,
                  y: 0,
                  transition: { duration: 0.7, ease: EASE, delay: 0.14 },
                },
              })}
        >
          <Text size="lg" c="gray.6" maw={600} lh={1.6} style={{ margin: "0 auto" }}>
            Upload anything. Get exam-style questions, scoped exactly to what you
            read, with a tutor that knows your source. Measure and improve
            understanding — one question at a time.
          </Text>
        </motion.div>

        <motion.div
          {...(reduce
            ? { initial: false as const, animate: undefined }
            : {
                initial: { opacity: 0, y: 16 },
                animate: {
                  opacity: 1,
                  y: 0,
                  transition: { duration: 0.7, ease: EASE, delay: 0.22 },
                },
              })}
        >
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
        </motion.div>

        <WavyTextAnimation />

        <Box w="100%" maw={640} style={{ marginTop: 12 }}>
          <motion.div
            initial={reduce ? false : { opacity: 0, scale: 0.98, y: 20 }}
            animate={
              reduce
                ? undefined
                : {
                    opacity: 1,
                    scale: 1,
                    y: 0,
                    transition: { duration: 0.8, ease: EASE, delay: 0.28 },
                  }
            }
          >
            <ProductMock />
          </motion.div>
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
