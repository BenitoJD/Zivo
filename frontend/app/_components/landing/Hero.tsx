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
import { GradientBackdrop } from "./GradientBackdrop";
import { GenerationAnimation } from "./GenerationAnimation";
import { ProductMock } from "./ProductMock";

const PLATFORMS = [
  { icon: IconBrandApple, label: "Mac" },
  { icon: IconBrandWindows, label: "Windows" },
  { icon: IconDeviceMobile, label: "iPhone" },
  { icon: IconBrandAndroid, label: "Android" },
];

/**
 * Wispr-Flow-style hero. A soft animated mesh-gradient sits behind a big, bold
 * sans headline (not serif - Wispr's signature), a lavender-green pill CTA, a
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

          {/* Serif display headline - Wispr's two-weight treatment: a muted
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
            read - with a tutor that knows your source. Measure and improve
            understanding, one question at a time.
          </Text>

          <Stack gap={18} align="center" w="100%">
            <Group gap="md" wrap="wrap" justify="center" w="100%">
              <Button
                size="lg"
                component={Link}
                href="/workspace"
                rightSection={<IconArrowRight size={18} stroke={2} />}
                className="hero-cta-primary"
                w={{ base: "100%", md: "auto" }}
              >
                Start studying - it&apos;s free
              </Button>
              <Button
                size="lg"
                variant="default"
                component={Link}
                href="/signup"
                className="hero-cta-secondary"
                w={{ base: "100%", md: "auto" }}
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

          <GenerationAnimation />

          <Box w="100%" maw={680} mt={8}>
            <ProductMock />
          </Box>
        </Stack>
      </Container>

      <style>{`
        /* Wispr "Download for macOS" - soft lavender pill (matches the nav CTA). */
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
