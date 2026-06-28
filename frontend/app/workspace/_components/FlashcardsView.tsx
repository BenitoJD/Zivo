"use client";

import { useState } from "react";
import {
  Badge,
  Box,
  Button,
  Center,
  Group,
  Stack,
  Text,
  ThemeIcon,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconArrowRight,
  IconCards,
  IconRotateClockwise,
} from "@tabler/icons-react";
import { useFlashcardsQuery } from "@/lib/api/queries";
import { GenerateGate, useGenStarted } from "./GenerateGate";
import { WaitState } from "./WaitState";

/**
 * Flashcards mode — Scribely-style active recall. A flip-card deck generated from the
 * source: tap to reveal the answer, arrow through the deck. Additive to MCQ.
 */
export function FlashcardsView({
  artifactId,
  compact = false,
}: {
  artifactId: string;
  compact?: boolean;
}) {
  const [started, start] = useGenStarted(artifactId, "cards");

  const { data, isError, refetch } = useFlashcardsQuery(artifactId, started);
  const status = data?.status;
  const cards = data?.cards ?? [];
  const ready = status === "ready" && cards.length > 0;

  const [i, setI] = useState(0);
  const [flipped, setFlipped] = useState(false);

  if (!started && cards.length === 0) {
    return (
      <GenerateGate
        icon={<IconCards size={28} />}
        title="Make flashcards"
        description="Create flashcards to drill the key facts."
        onStart={start}
        compact={compact}
      />
    );
  }

  if (isError || status === "failed") {
    return (
      <WaitState
        icon={<IconAlertTriangle size={26} />}
        title="Couldn’t build the cards"
        body="Something went wrong reading this material. Try again in a moment."
        action={
          <Button variant="light" color="lavender" radius="xl" onClick={() => void refetch()}>
            Try again
          </Button>
        }
      />
    );
  }

  if (!ready) {
    return (
      <WaitState
        pet
        title="Making your flashcards"
        body="Pulling the key facts into active-recall cards — this takes a few moments…"
      />
    );
  }

  const idx = Math.min(i, cards.length - 1);
  const card = cards[idx];
  const go = (delta: number) => {
    setFlipped(false);
    setI((cur) => (Math.min(cur, cards.length - 1) + delta + cards.length) % cards.length);
  };

  return (
    <Stack gap="lg" align="center" pb="xl" w="100%">
      <Group gap="xs" justify="center">
        <ThemeIcon variant="light" color="lavender" radius="xl" size="md">
          <IconCards size={16} />
        </ThemeIcon>
        <Text c="dimmed" fz="sm" fw={600} ff="monospace">
          {String(idx + 1).padStart(2, "0")} / {String(cards.length).padStart(2, "0")}
        </Text>
      </Group>

      <Box
        role="button"
        tabIndex={0}
        onClick={() => setFlipped((f) => !f)}
        onKeyDown={(e) => {
          if (e.key === " " || e.key === "Enter") {
            e.preventDefault();
            setFlipped((f) => !f);
          }
          if (e.key === "ArrowRight") go(1);
          if (e.key === "ArrowLeft") go(-1);
        }}
        w="100%"
        maw={620}
        style={{ perspective: 1400, cursor: "pointer", outline: "none" }}
      >
        <Box
          style={{
            position: "relative",
            width: "100%",
            minHeight: compact ? 240 : 300,
            transformStyle: "preserve-3d",
            transition: "transform 480ms cubic-bezier(0.32,0.72,0,1)",
            transform: flipped ? "rotateY(180deg)" : "rotateY(0deg)",
          }}
        >
          <CardFace face="front" kind={card.kind} text={card.front} compact={compact} />
          <CardFace face="back" kind={card.kind} text={card.back} compact={compact} flipped />
        </Box>
      </Box>

      <Group gap="sm" wrap="nowrap">
        <Button
          variant="default"
          radius="xl"
          leftSection={<IconArrowLeft size={16} />}
          onClick={() => go(-1)}
        >
          Prev
        </Button>
        <Button
          variant="light"
          color="lavender"
          radius="xl"
          leftSection={<IconRotateClockwise size={16} />}
          onClick={() => setFlipped((f) => !f)}
        >
          Flip
        </Button>
        <Button
          variant="default"
          radius="xl"
          rightSection={<IconArrowRight size={16} />}
          onClick={() => go(1)}
        >
          Next
        </Button>
      </Group>
    </Stack>
  );
}

function CardFace({
  face,
  kind,
  text,
  compact,
  flipped = false,
}: {
  face: "front" | "back";
  kind: "qa" | "cloze";
  text: string;
  compact?: boolean;
  flipped?: boolean;
}) {
  const isBack = face === "back";
  return (
    <Box
      style={{
        position: isBack ? "absolute" : "relative",
        inset: 0,
        backfaceVisibility: "hidden",
        WebkitBackfaceVisibility: "hidden",
        transform: flipped ? "rotateY(180deg)" : undefined,
        borderRadius: "var(--mantine-radius-xl)",
        border: "1px solid var(--app-border, var(--mantine-color-gray-2))",
        background: isBack
          ? "var(--mantine-color-lavender-0)"
          : "var(--mantine-color-body)",
        boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: compact ? 24 : 36,
        minHeight: compact ? 240 : 300,
      }}
    >
      <Badge
        variant="light"
        color={isBack ? "lavender" : "gray"}
        radius="sm"
        size="xs"
        style={{ position: "absolute", top: 14, left: 16 }}
      >
        {isBack ? "Answer" : kind === "cloze" ? "Fill in the blank" : "Question"}
      </Badge>
      <Text
        ta="center"
        ff="var(--font-serif)"
        fz={compact ? 18 : 22}
        fw={500}
        lh={1.45}
        c="var(--mantine-color-text)"
      >
        {text}
      </Text>
      {!isBack ? (
        <Text c="dimmed" fz="xs" mt="lg" style={{ position: "absolute", bottom: 14 }}>
          Tap to reveal
        </Text>
      ) : null}
    </Box>
  );
}