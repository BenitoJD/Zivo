"use client";

import Link from "next/link";
import { Box, Button, Container, Group, Stack, Text, Title } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { motion, useReducedMotion } from "framer-motion";
import { EASE } from "./motion";
import { ProductMock } from "./ProductMock";

/**
 * Editorial hero: oversized serif headline with an italicised, lavender-tinted
 * key phrase; calm subcopy; dual CTA + a quiet trust badge. Right column is the
 * living ProductMock. Reduces to a single centered column on small screens.
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
      <Group align="center" grow wrap="nowrap" gap={48}>
        <Stack gap={26} style={{ maxWidth: 600 }}>
          <motion.div {...enter}>
            <Text
              size="sm"
              fw={600}
              tt="uppercase"
              lts={2}
              c="lavender.7"
              style={{ fontFamily: "var(--font-sans)" }}
            >
              Question Better.
            </Text>
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
                lineHeight: 1.06,
                fontSize: "clamp(2.4rem, 6vw, 4.25rem)",
                letterSpacing: "-0.025em",
              }}
            >
              Turn what you read into{" "}
              <Box
                component="span"
                fs="italic"
                c="lavender.7"
                style={{ fontFamily: "var(--font-serif), Georgia, serif" }}
              >
                questions
              </Box>{" "}
              that actually test it.
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
            <Text size="lg" c="gray.6" maw={520} lh={1.6}>
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
            <Stack gap={14}>
              <Group gap="sm" wrap="nowrap">
                <Button
                  size="lg"
                  component={Link}
                  href="/workspace"
                  rightSection={<IconArrowRight size={18} stroke={1.75} />}
                >
                  Start studying
                </Button>
                <Button
                  size="lg"
                  variant="subtle"
                  color="gray"
                  component={Link}
                  href="/signup"
                >
                  Create an account
                </Button>
              </Group>
              <Group gap={6} wrap="nowrap">
                <Text size="xs" c="gray.5">
                  Free to start
                </Text>
                <Text size="xs" c="gray.4">
                  ·
                </Text>
                <Text size="xs" c="gray.5">
                  No credit card
                </Text>
                <Text size="xs" c="gray.4">
                  ·
                </Text>
                <Text size="xs" c="gray.5">
                  First question in under a minute
                </Text>
              </Group>
            </Stack>
          </motion.div>
        </Stack>

        <Box visibleFrom="md">
          <motion.div
            initial={reduce ? false : { opacity: 0, scale: 0.98, y: 14 }}
            animate={
              reduce
                ? undefined
                : {
                    opacity: 1,
                    scale: 1,
                    y: 0,
                    transition: { duration: 0.8, ease: EASE, delay: 0.18 },
                  }
            }
          >
            <ProductMock />
          </motion.div>
        </Box>
      </Group>
    </Container>
  );
}
