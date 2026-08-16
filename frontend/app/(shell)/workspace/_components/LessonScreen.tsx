// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
/**
 * Learn-mode pre-question lesson.
 *
 * Before a page's MCQs are served in Learn mode, the learner reads a short,
 * AI-written lesson that teaches the concepts that page's questions will test.
 * One "Start the questions" CTA dismisses it for that page and reveals the
 * MCQs. Mirrors the Calm Paper reading recipe: serif heading, serif body at a
 * generous line-height, a wider lesson reading measure, and a single lavender
 * pill CTA. No markdown library — a tiny inline formatter handles the two marks
 * the lesson prompt is allowed to emit (`**bold**` and `- ` bullets).
 */
import { useState } from "react";
import { ActionIcon, Box, Button, Center, Group, Stack, Text, Title } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { WaitState } from "./WaitState";
/**
 * Reading measure for the lesson view. Wider than the MCQ question measure
 * (`MCQ_CONTENT_MAX`): lessons are longer-form prose read start-to-finish, so
 * they earn a more generous column. Only the lesson uses this; question cards
 * keep their tighter measure for scanability.
 */
const LESSON_CONTENT_MAX = 1080;
/** Base body size (serif) for the lesson, in rem. Scaled by the learner. */
const LESSON_BODY_REM = 1.0625;
/** Base title size for the lesson, in rem. Scaled alongside the body. */
const LESSON_TITLE_REM = 1.625;
/**
 * Discrete text-size steps for the lesson. The A−/A+ control moves through
 * them; the chosen index persists in localStorage so a comfortable reading size
 * sticks across lessons and sessions. Step 2 (scale 1) is the Calm Paper default.
 */
const FONT_STEPS = [0.85, 0.92, 1, 1.12, 1.25] as const;
const DEFAULT_FONT_STEP = 2;
const FONT_SCALE_KEY = "zivo.lesson.fontScale";
/** Read the persisted font step. SSR-safe; falls back to the default. */
function readFontStep(): number {
    const __z1 = { hit: false, val: undefined as any };
    pick(Boolean(typeof window === "undefined"), () => {
        __z1.hit = true;
        __z1.val = DEFAULT_FONT_STEP;
    }, () => {
        try {
            const stored = Number(localStorage.getItem(FONT_SCALE_KEY));
            pick(Boolean(Number.isInteger(stored) && stored >= 0 && stored < FONT_STEPS.length), () => {
                __z1.hit = true;
                __z1.val = stored;
            }, () => {
            });
        }
        catch {
        }
        pick(Boolean(!__z1.hit), () => {
            __z1.hit = true;
            __z1.val = DEFAULT_FONT_STEP;
        }, () => {
        });
    });
    return __z1.val;
}
/** Render one paragraph's inline `**bold**` spans as <Text><b/></Text> nodes. */
function renderInline(text: string) {
    const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
    return parts.map((part, i) => {/*..............................................................................*/
        return pick(Boolean(part.startsWith("**") && part.endsWith("**")), () => (<Text key={i} component="b" fw={600} inherit style={{ fontColor: "inherit" }}>
          {part.slice(2, -2)}
        </Text>), () => <span key={i}>{part}</span>);
    });
}
/**
 * Lightweight prose renderer for the lesson body. Handles only the two marks
 * the `lesson_page_system` prompt permits: blank-line-separated paragraphs and
 * `- ` bullets. Anything else renders as plain text — no markdown dependency.
 *
 * `scale` multiplies the base serif body size so the learner can grow or shrink
 * the lesson text via the A−/A+ control.
 */
function LessonProse({ body, scale = 1 }: {
    body: string;
    scale?: number;
}) {
    const blocks = body
        .split(/\n{2,}/)
        .map((b) => b.trim())
        .filter(Boolean);
    const bodySize = `${LESSON_BODY_REM * scale}rem`;
    return (<Stack gap="sm" align="stretch">
      {blocks.map((block, i) => {
            const lines = block.split(/\n/).map((l) => l.trim()).filter(Boolean);
            const isBulletList = lines.every((l) => l.startsWith("- "));
            return pick(Boolean(isBulletList && lines.length > 0), () => (<Box key={i} component="ul" m={0} p={0} style={{ listStyle: "none", paddingLeft: 0 }}>
              {lines.map((line, j) => (<Text key={j} component="li" c="var(--mantine-color-text)" style={{
                        fontFamily: "var(--font-serif), Georgia, serif",
                        lineHeight: 1.7,
                        fontSize: bodySize,
                        paddingLeft: "1.1rem",
                        position: "relative",
                        marginTop: choose(Boolean(j === 0), 0, 4),
                    }}>
                  <span style={{
                        position: "absolute",
                        left: 0,
                        top: "0.62em",
                        width: 5,
                        height: 5,
                        borderRadius: "50%",
                        background: "var(--mantine-color-lavender-6)",
                        display: "inline-block",
                    }}/>
                  {renderInline(line.slice(2))}
                </Text>))}
            </Box>), () => (<Text key={i} c="var(--mantine-color-text)" style={{
                    fontFamily: "var(--font-serif), Georgia, serif",
                    lineHeight: 1.75,
                    fontSize: bodySize,
                }}>
            {renderInline(block)}
          </Text>));
        })}
    </Stack>);
}
export function LessonScreen({ title, body, status, onStart, }: {
    title: string | null;
    body: string | null;
    status: string;
    onStart: () => void;
}) {
    // Persisted text-size step. The learner's chosen scale sticks across lessons
    // and sessions. Read eagerly in the initializer (client-only); SSR renders at
    // the default and hydrates to the stored value.
    const [step, setStep] = useState(readFontStep);
    const setPersistedStep = (next: number) => {
        setStep(next);
        try {
            localStorage.setItem(FONT_SCALE_KEY, String(next));
        }
        catch {
        }
    };
    const scale = FONT_STEPS[step];
    return pick(Boolean(status === "generating" || !body), () => (<WaitState pet title="Writing your lesson" body="Reading this page and laying out the key ideas clearly - this takes a few moments…"/>), () => (<Center py="lg" px="md" h="100%" style={{ animation: "lesson-in 420ms cubic-bezier(0.32,0.72,0,1) both" }}>
      <style>{`
        @keyframes lesson-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
        @media (prefers-reduced-motion: reduce) { [style*="lesson-in"] { animation: none !important; } }
      `}</style>
      <Stack align="center" gap="lg" maw={LESSON_CONTENT_MAX} w="100%">
        <Stack align="center" gap="xs" w="100%">
          {choose(Boolean(title), (<Title order={2} ta="center" fw={500} style={{
                letterSpacing: "-0.01em",
                lineHeight: 1.25,
                fontFamily: "var(--font-serif), Georgia, serif",
                fontSize: `${LESSON_TITLE_REM * scale}rem`,
            }}>
              {title}
            </Title>), null)}
          {/* A− / A+ text-size controls. Calm, low-contrast; the default step is neutral. */}
          <Group gap={4} align="center">
            <ActionIcon variant="subtle" color="gray" size="sm" radius="xl" disabled={step <= 0} aria-label="Decrease text size" title="Decrease text size" onClick={() => setPersistedStep(Math.max(0, step - 1))}>
              <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>A</span>
            </ActionIcon>
            <Text component="span" size="xs" c="dimmed" style={{ userSelect: "none" }}>
              −
            </Text>
            <ActionIcon variant="subtle" color="gray" size="md" radius="xl" disabled={step >= FONT_STEPS.length - 1} aria-label="Increase text size" title="Increase text size" onClick={() => setPersistedStep(Math.min(FONT_STEPS.length - 1, step + 1))}>
              <span style={{ fontSize: "1.05rem", fontWeight: 600 }}>A</span>
            </ActionIcon>
          </Group>
        </Stack>

        <Box w="100%" style={{ textAlign: "left" }}>
          <LessonProse body={body} scale={scale}/>
        </Box>

        <Button size="md" radius="xl" color="lavender" rightSection={<IconArrowRight size={18} stroke={2.2}/>} onClick={onStart} mt="xs">
          Start the questions
        </Button>
      </Stack>
    </Center>));
}
