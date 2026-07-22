"use client";

import { useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Box, Group, Paper, Stack, Text, ThemeIcon, Title } from "@mantine/core";
import { IconBulb, IconMessageCircle, IconUpload } from "@tabler/icons-react";
import { SourceImportDeck } from "@/app/workspace/_components/SourceImportDeck";

const STEPS = [
  {
    icon: IconUpload,
    title: "1. Index",
    body: "Upload lecture notes, PDFs, or articles.",
    color: "lavender",
  },
  {
    icon: IconBulb,
    title: "2. Practice",
    body: "Answer questions generated from the text.",
    color: "sage",
  },
  {
    icon: IconMessageCircle,
    title: "3. Perfect",
    body: "Resolve misconceptions with your source tutor.",
    color: "lavender",
  },
];

export default function WorkspaceIndexPage() {
  const router = useRouter();

  // Interactive spotlight is driven through CSS variables on a ref (rAF-throttled)
  // rather than React state, so moving the mouse never re-renders the page.
  const glowRef = useRef<HTMLDivElement>(null);
  const glowRafRef = useRef<number | null>(null);

  const handleMouseMove = (e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 100;
    const y = ((e.clientY - rect.top) / rect.height) * 100;
    if (glowRafRef.current != null) return;
    glowRafRef.current = requestAnimationFrame(() => {
      glowRafRef.current = null;
      const el = glowRef.current;
      if (!el) return;
      el.style.setProperty("--zx", `${x}%`);
      el.style.setProperty("--zy", `${y}%`);
    });
  };

  useEffect(() => {
    return () => {
      if (glowRafRef.current != null) cancelAnimationFrame(glowRafRef.current);
    };
  }, []);

  return (
    <Box
      flex={1}
      px="lg"
      py="xl"
      onMouseMove={handleMouseMove}
      style={{
        position: "relative",
        overflowY: "auto",
        background: "var(--mantine-color-body)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {/* Ambient interactive light glow - position fed via --zx/--zy CSS vars. */}
      <Box
        ref={glowRef}
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          zIndex: 0,
          background:
            "radial-gradient(circle 500px at var(--zx, 50%) var(--zy, 50%), rgba(123, 93, 166, 0.04), transparent 70%)",
        }}
      />

      <Stack align="center" gap={36} w="100%" maw={720} style={{ position: "relative", zIndex: 1 }}>
        {/* Editorial Header */}
        <Stack align="center" gap={8} ta="center">
          <Box
            style={{
              display: "inline-flex",
              alignItems: "center",
              padding: "4px 12px",
              borderRadius: "var(--mantine-radius-xl)",
              background: "var(--mantine-color-lavender-0)",
              border: "1px solid var(--mantine-color-lavender-2)",
            }}
          >
            <Text size="xs" fw={700} lts={1} c="lavender.7">
              STUDY DECK
            </Text>
          </Box>
          <Title
            order={2}
            style={{
              fontFamily: "var(--font-sans), sans-serif",
              fontWeight: 500,
              letterSpacing: "-0.02em",
              fontSize: "clamp(1.8rem, 4.5vw, 2.6rem)",
            }}
          >
            Where does understanding{" "}
            <Box component="span" c="lavender.7" style={{ fontWeight: 700 }}>
              begin?
            </Box>
          </Title>
          <Text size="sm" c="gray.6" maw={480} lh={1.6}>
            Drag in a file, import a link, or paste notes directly. Zivo will index your content and build tailored study loops.
          </Text>
        </Stack>

        {/* Main interaction deck - shared with the sidebar Add-source modal. */}
        <SourceImportDeck onImported={(id) => router.push(`/workspace/${id}`)} />

        {/* Workflow steps timeline */}
        <Group grow align="stretch" gap="md" w="100%" visibleFrom="sm">
          {STEPS.map((s) => (
            <Paper
              key={s.title}
              radius="lg"
              p="lg"
              withBorder
              bg="gray.0"
              style={{
                border: "1px solid var(--mantine-color-default-border)",
                cursor: "default",
                transition: "transform 280ms cubic-bezier(0.32, 0.72, 0, 1), box-shadow 280ms cubic-bezier(0.32, 0.72, 0, 1)",
              }}
              className="step-timeline-card"
            >
              <Stack gap={10} align="center" ta="center">
                <ThemeIcon
                  size={40}
                  radius="xl"
                  variant="light"
                  color={s.color}
                  style={{
                    background: `var(--mantine-color-${s.color}-0)`,
                    border: `1px solid var(--mantine-color-${s.color}-2)`,
                  }}
                >
                  <s.icon size={20} style={{ color: `var(--mantine-color-${s.color}-7)` }} />
                </ThemeIcon>
                <Text fw={600} size="sm" style={{ fontFamily: "var(--font-sans), sans-serif" }}>
                  {s.title}
                </Text>
                <Text size="xs" c="gray.6" lh={1.5}>
                  {s.body}
                </Text>
              </Stack>
            </Paper>
          ))}
        </Group>
      </Stack>

      <style>{`
        .step-timeline-card:hover {
          transform: translateY(-3px);
          box-shadow: var(--mantine-shadow-paper-lg) !important;
        }
      `}</style>
    </Box>
  );
}
