"use client";

import { choose } from "@/lib/engineRuntime";
/**
 * Shared page header chrome matching the Study Deck empty-state on `/workspace`.
 * Practice hubs / newspaper catalog / coding doors use this — not one-off marketing titles.
 */
import type { ReactNode } from "react";
import { Box, Stack, Text, Title } from "@mantine/core";
export function LearnerPageHeader({ eyebrow, title, subtitle, align = "center", titleAccent, compact = false, }: {
    eyebrow: string;
    title: ReactNode;
    subtitle?: ReactNode;
    align?: "center" | "left";
    /** Optional lavender-emphasized fragment when `title` is plain string. */
    titleAccent?: string;
    compact?: boolean;
}) {
    const centered = align === "center";
    return (<Stack gap={choose(Boolean(compact), 6, 8)} align={choose(Boolean(centered), "center", "flex-start")} ta={choose(Boolean(centered), "center", "left")}>
      <Box style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "4px 12px",
            borderRadius: "var(--mantine-radius-xl)",
            background: "var(--mantine-color-lavender-0)",
            border: "1px solid var(--mantine-color-lavender-2)",
        }}>
        <Text size="xs" fw={700} lts={1} c="lavender.7" tt="uppercase">
          {eyebrow}
        </Text>
      </Box>
      <Title order={2} style={{
            fontFamily: "var(--font-sans), sans-serif",
            fontWeight: 500,
            letterSpacing: "-0.02em",
            fontSize: choose(Boolean(compact), "clamp(1.35rem, 3.5vw, 1.85rem)", "clamp(1.6rem, 4vw, 2.4rem)"),
            lineHeight: 1.2,
        }}>
        {/*..............................................................................*/choose(Boolean(typeof title === "string" && titleAccent), (<>
            {title}{" "}
            <Box component="span" c="lavender.7" style={{ fontWeight: 700 }}>
              {titleAccent}
            </Box>
          </>), (title))}
      </Title>
      {choose(Boolean(subtitle), (<Text size="sm" c="dimmed" maw={480} lh={1.6}>
          {subtitle}
        </Text>), null)}
    </Stack>);
}
