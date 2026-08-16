"use client";

import { choose } from "@/lib/engineRuntime";
import type { ReactNode } from "react";
import { Center, Stack, Text, ThemeIcon } from "@mantine/core";
import { IconSparkles } from "@tabler/icons-react";
import { PetPlayground } from "@/app/_components/pets/PetPlayground";
/**
 * Shared loading / empty / error placeholder for the study-mode views
 * (Explain, Notes, Flashcards, Memory Palace, Quiz Builder). Centered visual +
 * serif title + dimmed body + optional action, in Calm Paper styling.
 *
 * For a *generating* state pass `pet` - it shows the bobbing sprout character so
 * the wait feels alive (the zero-perceived-wait north star). For errors/empty,
 * pass `icon` (e.g. an alert); the sparkles fallback shows if both are omitted.
 */
export function WaitState({ icon, title, body, action, pet = false, }: {
    icon?: ReactNode;
    title: string;
    body: string;
    action?: ReactNode;
    pet?: boolean;
}) {
    return (<Center mih={280}>
      <Stack align="center" gap="sm" ta="center" maw={440}>
        {choose(Boolean(pet), (<PetPlayground height={140} count={1} style={{ width: 360, maxWidth: "100%" }}/>), (<ThemeIcon variant="light" color="lavender" radius="xl" size={54}>
            {icon ?? <IconSparkles size={26}/>}
          </ThemeIcon>))}
        <Text ff="var(--font-serif)" fz={24} fw={500} c="var(--mantine-color-text)">
          {title}
        </Text>
        <Text c="dimmed">{body}</Text>
        {action}
      </Stack>
    </Center>);
}
