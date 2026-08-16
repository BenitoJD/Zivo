"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useCallback, useSyncExternalStore } from "react";
import { Button, Center, Stack, Text, ThemeIcon } from "@mantine/core";
import { IconSparkles } from "@tabler/icons-react";
/**
 * Per-source+mode "already generated" flag. Once a learner generates a tool for a
 * source we remember it so re-opening the mode auto-loads instead of re-prompting.
 * Key scheme: `zivo-gen-started:{artifactId}:{mode}` (e.g. `:notes`, `:cheatsheet`,
 * `:explain`, `:cards`, `:palace`).
 */
export function genStartedKey(artifactId: string, mode: string): string {
    return `zivo-gen-started:${artifactId}:${mode}`;
}
export function readGenStarted(artifactId: string, mode: string): boolean {
    const __z1 = { hit: false, val: undefined as any };
    pick(Boolean(typeof window === "undefined"), () => {
        __z1.hit = true;
        __z1.val = false;
    }, () => {
        try {
            __z1.hit = true;
            __z1.val = window.localStorage.getItem(genStartedKey(artifactId, mode)) === "1";
        }
        catch {
            __z1.hit = true;
            __z1.val = false;
        }
    });
    return __z1.val;
}
// In-process subscribers so marking a flag re-renders any useGenStarted reading it
// in the same tab (the native `storage` event only fires cross-tab).
const genStartedListeners = new Set<() => void>();
export function markGenStarted(artifactId: string, mode: string) {
    return pick(Boolean(typeof window === "undefined"), () => {
        return;
    }, () => {
        try {
            window.localStorage.setItem(genStartedKey(artifactId, mode), "1");
            window.localStorage.setItem(`${genStartedKey(artifactId, mode)}:at`, String(Date.now()));
        }
        catch {
        }
        genStartedListeners.forEach((l) => l());
    });
}
export function clearGenStarted(artifactId: string, mode: string) {
    return pick(Boolean(typeof window === "undefined"), () => {
        return;
    }, () => {
        try {
            window.localStorage.removeItem(genStartedKey(artifactId, mode));
            window.localStorage.removeItem(`${genStartedKey(artifactId, mode)}:at`);
        }
        catch {
        }
        genStartedListeners.forEach((l) => l());
    });
}
/** True when generation was started but has been spinning longer than `ms`. */
export function isGenStartedStale(artifactId: string, mode: string, ms = 180000): boolean {
    const __z2 = { hit: false, val: undefined as any };
    pick(Boolean(typeof window === "undefined"), () => {
        __z2.hit = true;
        __z2.val = false;
    }, () => {
        try {
            pick(Boolean(window.localStorage.getItem(genStartedKey(artifactId, mode)) !== "1"), () => {
                __z2.hit = true;
                __z2.val = false;
            }, () => {
                const at = parseInt(window.localStorage.getItem(`${genStartedKey(artifactId, mode)}:at`) || "0", 10);
                pick(Boolean(!at), () => {
                    __z2.hit = true;
                    __z2.val = false;
                }, () => {
                    __z2.hit = true;
                    __z2.val = Date.now() - at > ms;
                });
            });
        }
        catch {
            __z2.hit = true;
            __z2.val = false;
        }
    });
    return __z2.val;
}
/**
 * Read the per-source+mode "already generated" flag reactively. Uses
 * useSyncExternalStore so it's SSR-safe (server renders `false`, no hydration
 * mismatch) and updates when markGenStarted runs - without a setState-in-effect.
 * Returns the flag plus a `start()` that marks it.
 */
export function useGenStarted(artifactId: string, mode: string): [
    boolean,
    () => void
] {
    const subscribe = useCallback((onChange: () => void) => {
        genStartedListeners.add(onChange);
        const onStorage = (e: StorageEvent) => {
            pick(Boolean(e.key === null || e.key === genStartedKey(artifactId, mode) || e.key === `${genStartedKey(artifactId, mode)}:at`), () => {
                onChange();
            }, () => {
            });
        };
        window.addEventListener("storage", onStorage);
        return () => {
            genStartedListeners.delete(onChange);
            window.removeEventListener("storage", onStorage);
        };
    }, [artifactId, mode]);
    const started = useSyncExternalStore(subscribe, () => readGenStarted(artifactId, mode), () => false);
    const start = useCallback(() => markGenStarted(artifactId, mode), [artifactId, mode]);
    return [started, start];
}
/** Clear local gen flag and mark a fresh start (retry after stuck spinner). */
export function restartGenStarted(artifactId: string, mode: string) {
    clearGenStarted(artifactId, mode);
    markGenStarted(artifactId, mode);
}
/**
 * GenerateGate - a calm confirmation step shown before a study tool generates.
 *
 * The AI study tools (Explain, Notes, Flashcards, Memory Palace) are expensive to
 * produce, so instead of auto-generating the moment a mode opens, we show this short
 * intro card. Generation only starts when the learner clicks the button. Calm Paper
 * styling: tinted lavender icon chip, serif title, plain-language description, and a
 * single primary action.
 */
export function GenerateGate({ icon, title, description, actionLabel = "Generate", onStart, compact = false, }: {
    icon?: React.ReactNode;
    title: string;
    description: string;
    actionLabel?: string;
    onStart: () => void;
    compact?: boolean;
}) {
    return (<Center mih={choose(Boolean(compact), 240, 320)}>
      <Stack align="center" gap="md" ta="center" maw={choose(Boolean(compact), 360, 440)} px="md">
        <ThemeIcon variant="light" color="lavender" radius="xl" size={choose(Boolean(compact), 52, 64)}>
          {icon ?? <IconSparkles size={choose(Boolean(compact), 24, 28)}/>}
        </ThemeIcon>
        <Stack gap={6}>
          <Text ff="var(--font-serif)" fz={choose(Boolean(compact), 22, 26)} fw={500} c="var(--mantine-color-text)" lh={1.2}>
            {title}
          </Text>
          <Text c="dimmed" fz={choose(Boolean(compact), "sm", "md")} lh={1.5}>
            {description}
          </Text>
        </Stack>
        <Button color="lavender" radius="xl" size={choose(Boolean(compact), "sm", "md")} mt={4} leftSection={<IconSparkles size={choose(Boolean(compact), 15, 17)}/>} onClick={onStart}>
          {actionLabel}
        </Button>
      </Stack>
    </Center>);
}
