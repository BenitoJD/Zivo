"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ActionIcon,
  Box,
  Button,
  Center,
  Group,
  Loader,
  Menu,
  Paper,
  Progress,
  SegmentedControl,
  Stack,
  Text,
  ThemeIcon,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { useInterval, useLocalStorage } from "@mantine/hooks";
import {
  IconArrowLeft,
  IconArrowRight,
  IconBulb,
  IconCheck,
  IconChevronDown,
  IconClipboardList,
  IconFlag,
  IconHistory,
  IconTarget,
  IconX,
} from "@tabler/icons-react";
import { GenerationStages } from "@/app/workspace/_components/GenerationStages";
import { PetPlayground } from "@/app/_components/pets/PetPlayground";
import { CAT_ENABLED_KEY } from "@/app/_components/pets/PetPlayground";
import {
  MCQ_CONTENT_MAX,
  MCQ_OPTION_FONT_SIZE,
  MCQ_STEM_FONT_SIZE,
  mcqOptionChrome,
  McqFeedbackCard,
} from "@/app/_components/mcq/McqCard";
import { formatMcqStemForDisplay, isStatementStyleStem } from "@/lib/mcqStemFormat";
import { normalizeMcqOptions, type McqState } from "@/lib/types";
import { learnWaitStatus } from "@/lib/learnStatus";
import { type AnsweredCard } from "@/app/workspace/_components/studyLayout";
import { useIsDark } from "@/lib/useIsDark";

/** Rotating status while the coaching feedback streams in - the message changes
 *  every ~1.3s (no static "…" spinner), so the wait feels alive. */
const FEEDBACK_WRITING_MESSAGES = [
  "Reading your answer",
  "Weighing your choice",
  "Checking the reasoning",
  "Finding the key idea",
  "Writing your feedback",
  "Almost there",
];

const CHECKING_MESSAGES = [
  "Reading your answer",
  "Weighing your choice",
  "One moment",
];

function RotatingStatusLine({
  messages,
  compact,
  className,
}: {
  messages: readonly string[];
  compact?: boolean;
  className?: string;
}) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setI((v) => (v + 1) % messages.length), 1300);
    return () => window.clearInterval(id);
  }, [messages]);
  return (
    <Group
      justify="center"
      mt={compact ? 8 : 12}
      className={className}
      style={{ flexShrink: 0, minHeight: 22 }}
    >
      <style>{`
        @keyframes zv-fb-rotate { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }
        .zv-fb-rotate { animation: zv-fb-rotate 320ms cubic-bezier(0.32,0.72,0,1) both; }
        @media (prefers-reduced-motion: reduce) { .zv-fb-rotate { animation: none !important; } }
      `}</style>
      <Text key={i} className="zv-fb-rotate" fz="sm" c="dimmed" fw={500}>
        {messages[i]}
      </Text>
    </Group>
  );
}

function FeedbackWritingStatus({ compact }: { compact?: boolean }) {
  return <RotatingStatusLine messages={FEEDBACK_WRITING_MESSAGES} compact={compact} />;
}

function McqCheckingStatus({ compact }: { compact?: boolean }) {
  return <RotatingStatusLine messages={CHECKING_MESSAGES} compact={compact} className="mcq-checking-status" />;
}

/**
 * MCQ panels (extracted from the workspace page monolith): the hero question card
 * (McqHeroPanel) - stem, options, checking/grading states, the waiting/generation
 * UI, keyboard control - and the step-back review of already-answered questions
 * (McqReviewView).
 */

/**
 * A scroll region with no visible scrollbar (which reads as distracting on the
 * question card). When there's more content below, a soft bottom fade and a
 * gently bouncing chevron cue the learner to scroll; both fade out at the end.
 */
function ScrollHintArea({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [showCue, setShowCue] = useState(false);

  const update = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const canScroll = el.scrollHeight - el.clientHeight > 6;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
    setShowCue(canScroll && !atBottom);
  }, []);

  useEffect(() => {
    update();
    const el = ref.current;
    if (!el) return;
    // Recompute when the region or its content resizes (e.g. an explanation reveals).
    const ro = new ResizeObserver(update);
    ro.observe(el);
    Array.from(el.children).forEach((c) => ro.observe(c));
    return () => ro.disconnect();
  }, [update]);

  return (
    <Box style={{ position: "relative", flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      <Box
        ref={ref}
        onScroll={update}
        className="zv-noscrollbar"
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          // `overflow-y: auto` forces overflow-x to compute to `auto` too (CSS won't
          // pair `visible` with a non-visible value), so this box clips horizontally
          // whether we ask it to or not. The option cards are full-width, which put
          // their 1px left/right borders exactly on that clip edge - visible top and
          // bottom, invisible at the sides. 4px is the minimum that clears the border
          // plus the arrow-key focus ring (2px outline at 2px offset, also being cut);
          // 6px leaves a little room for the hover lift.
          paddingInline: "clamp(6px, 2vw, 12px)",
        }}
      >
        {children}
      </Box>
      <Box
        aria-hidden
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: 52,
          pointerEvents: "none",
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "center",
          paddingBottom: 2,
          background: "linear-gradient(to bottom, transparent, var(--mantine-color-body) 80%)",
          opacity: showCue ? 1 : 0,
          transition: "opacity 240ms ease",
        }}
      >
        <IconChevronDown className="zv-scroll-cue" size={22} stroke={2} style={{ color: "var(--mantine-color-dimmed)" }} />
      </Box>
    </Box>
  );
}

export function McqHeroPanel({
  stem,
  options,
  selected,
  onSelect,
  multiSelect = false,
  selectedIndices,
  onToggle,
  feedback,
  mcqLoading,
  artifactStatus,
  indexProgress,
  hasQuestion,
  queue,
  mode,
  gradeState,
  submitting,
  compact = false,
  canReview = false,
  onReviewPrevious,
  onSubmit,
  onContinue,
  onAdvance,
  onRetry,
  onFlagQuestion,
  flagBusy = false,
  flagged = false,
  isNewspaper = false,
  confidence = null,
  onConfidenceChange,
  focusConcept,
  onFocusConcept,
}: {
  stem: string;
  options: string[];
  selected: string | null;
  onSelect: (value: string) => void;
  /** Multi-select ("select all that apply") mode: options toggle on/off. */
  multiSelect?: boolean;
  selectedIndices?: number[];
  onToggle?: (index: number) => void;
  feedback: string | null;
  mcqLoading: boolean;
  artifactStatus?: string;
  indexProgress?: number;
  hasQuestion?: boolean;
  queue?: McqState | null;
  mode: "learn" | "test";
  gradeState: { correct: boolean; correctIndex: number; correctIndices?: number[] } | null;
  submitting: boolean;
  compact?: boolean;
  canReview?: boolean;
  onReviewPrevious?: () => void;
  onSubmit: () => void;
  onContinue: () => void;
  onAdvance?: () => void;
  onRetry?: () => void;
  /** Report a bad / ambiguous question (Learn). */
  onFlagQuestion?: (reason: string) => void;
  flagBusy?: boolean;
  flagged?: boolean;
  /** Pre-cooked newspaper — no upload-style generation stages. */
  isNewspaper?: boolean;
  /** Calibration: learner's stated confidence before reveal (Learn only). */
  confidence?: number | null;
  onConfidenceChange?: (value: number) => void;
  /** Gap→focus: current question's concept label (Learn, after a miss). */
  focusConcept?: string | null;
  onFocusConcept?: (concept: string) => void;
}) {
  const isDark = useIsDark();
  const displayStem = formatMcqStemForDisplay(stem);
  const statementStem = isStatementStyleStem(displayStem);
  // Roaming study cat - opt-in (off by default); toggled in Settings and applied live.
  const [catEnabled] = useLocalStorage({ key: CAT_ENABLED_KEY, defaultValue: false });
  const safeOptions = normalizeMcqOptions(options);
  const graded = gradeState !== null;
  const showNextQuestion = graded;
  const optionsLocked = graded && (mode === "test" || gradeState.correct);
  const multiChosen = selectedIndices ?? [];
  // A multi-select answer set is "correct enough to lock" only when it's actually
  // correct; a single answer locks per the existing rule above.
  const hasSelection = multiSelect ? multiChosen.length > 0 : selected !== null;
  // Learn vs Test, the core distinction: Learn reveals the answer + explanation
  // right away (and lets you retry); Test records your choice silently and grades
  // everything at the very end - no peeking. `reveal` gates every "show the answer"
  // affordance so the two modes genuinely feel different.
  const isTest = mode === "test";
  const reveal = graded && !isTest;
  const accent = isTest ? "forest" : "lavender";
  // "Checking" = answer submitted, grade not back yet. We light up the chosen option
  // with a calm pulse so the wait never feels frozen.
  const checking = submitting && !graded;
  // Full-screen wait ONLY when the pool has nothing for the learner yet.
  // If learn-queue already has a next assertion (or unanswered generated cards),
  // advance instantly — never flash "Writing questions" over a ready card
  // (regression: 92% ring + "2 ready" while blocking).
  const newspaperReady = isNewspaper && artifactStatus === "ready";
  /** No question card on screen — show preparing chrome, not an empty hero + disabled buttons. */
  const showWaitChrome = !hasQuestion && !graded && !checking;

  const [statusTick, setStatusTick] = useState(0);
  const stagnant =
    showWaitChrome && Boolean(queue?.generation_pending) && (queue?.questions_generated ?? 0) === 0;
  // How long generation has been stuck with 0 questions produced, so we can offer a
  // retry after ~45s. Driven by an interval (not a ref read during render, which can
  // produce stale UI and is a React anti-pattern).
  const [stuckSeconds, setStuckSeconds] = useState(0);
  useEffect(() => {
    if (!stagnant) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reset the stuck-timer when stagnation flips (time-based state)
      setStuckSeconds(0);
      return;
    }
    const start = Date.now();
    setStuckSeconds(0);
    const id = window.setInterval(() => {
      setStuckSeconds(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, [stagnant]);
  const waitStatus = learnWaitStatus(
    {
      artifactStatus,
      indexProgress,
      mcqLoading,
      generationPending: queue?.generation_pending,
      pageTriageComplete: queue?.page_triage_complete,
      ragWindowReady: queue?.rag_window_ready,
      questionsGenerated: queue?.questions_generated,
      questionsAnswered: queue?.questions_answered,
      questionBudget: queue?.question_budget,
      poolAvailable: queue?.pool_available,
      isNewspaper,
    },
    statusTick,
  );
  // autoInvoke - without it Mantine's useInterval never starts, so the wait-status
  // copy never rotated. The `if (showWaitChrome)` guard keeps it a no-op while idle.
  useInterval(
    () => {
      if (showWaitChrome) setStatusTick((t) => t + 1);
    },
    1200,
    { autoInvoke: true },
  );
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset the loading-message rotation tick when the message changes
    setStatusTick(0);
  }, [waitStatus.rotateKey]);

  // Keyboard: A-D (or 1-4) to pick an option, Enter to check / advance.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (showWaitChrome) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      // Enter OR Space advances / checks - whichever hand is on the keyboard, no
      // reach for the mouse. A focused button/link keeps its native activation so
      // we never double-fire.
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        // After a grade, Enter/Space must always advance — even when focus is still
        // on the chosen option. Correct answers lock options; the old path returned
        // early on that focused+locked case, so "press Enter to continue" did nothing.
        if (graded) {
          e.preventDefault();
          onContinue();
          return;
        }
        if (checking) return;
        // Arrowing leaves focus ON an option, so this is now the common case. Drive
        // the option's own click handler and preventDefault to suppress the native
        // activation, rather than relying on that native activation to fire at all -
        // one click either way, and Enter behaves the same however you got here.
        const focusedOption = (document.activeElement as HTMLElement | null)?.closest<HTMLButtonElement>(
          "[data-mcq-option]",
        );
        if (focusedOption) {
          e.preventDefault();
          focusedOption.click();
          return;
        }
        if (tag === "BUTTON" || tag === "A") return;
        if (hasSelection && !submitting) {
          e.preventDefault();
          onSubmit();
        }
        return;
      }
      // Arrows step through the options. We move real DOM focus rather than keeping
      // a separate cursor in state: the options are <button>s, so Enter/Space then
      // activates the focused one natively (which is what makes this work for
      // select-all-that-apply, where arrowing must not toggle anything by itself).
      const dir =
        e.key === "ArrowDown" || e.key === "ArrowRight"
          ? 1
          : e.key === "ArrowUp" || e.key === "ArrowLeft"
            ? -1
            : 0;
      if (dir !== 0) {
        if (optionsLocked || checking) return;
        const nodes = Array.from(
          document.querySelectorAll<HTMLButtonElement>("[data-mcq-option]"),
        );
        if (nodes.length === 0) return;
        e.preventDefault();
        const focused = nodes.indexOf(document.activeElement as HTMLButtonElement);
        // Start from whatever is focused, else the current answer, else "before the
        // first" so Down lands on A and Up wraps to the last option.
        const from =
          focused >= 0 ? focused : !multiSelect && selected !== null ? Number(selected) : -1;
        const next = (from + dir + nodes.length) % nodes.length;
        nodes[next]?.focus();
        // Single-answer questions select as you move (a radio group). Multi-select
        // only moves focus - toggling every option you pass over would be destructive.
        if (!multiSelect) onSelect(String(next));
        return;
      }
      const k = e.key.toLowerCase();
      // Guard the single-character shape first: `"abcdef".indexOf("")` is 0, so any
      // keydown carrying an empty key (IME composition, some soft keyboards,
      // synthetic events) silently answered option A.
      if (k.length !== 1) return;
      let idx = "abcdef".indexOf(k);
      if (idx < 0 && /[1-9]/.test(k)) idx = Number(k) - 1;
      if (idx >= 0 && idx < safeOptions.length && !optionsLocked) {
        e.preventDefault();
        if (multiSelect) onToggle?.(idx);
        else onSelect(String(idx));
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [showWaitChrome, graded, hasSelection, submitting, optionsLocked, checking, selected, safeOptions.length, multiSelect, onSelect, onToggle, onSubmit, onContinue]);

  if (showWaitChrome) {
    // Determinate progress during generation: ring fills toward the FIRST
    // question (then the learner leaves this wait), not the full page budget —
    // a 400-idea plan must not pin the ring at ~48% for an hour.
    const generated = queue?.questions_generated ?? 0;
    const budget = queue?.plan_budget ?? queue?.question_budget ?? queue?.generation_cap ?? 0;
    const readingPhase = artifactStatus === "indexing" || queue?.rag_window_ready === false;
    const planningPhase = !readingPhase && !queue?.page_triage_complete;
    const progressPct = newspaperReady
      ? generated > 0
        ? 72
        : null
      : readingPhase
        ? Math.min(28, Math.round((indexProgress ?? 0) * 0.28))
        : planningPhase
          ? 40
          : generated > 0
            ? 92
            : 52;
    const RING = compact ? 108 : 120;
    const R = RING / 2 - 10;
    const CIRC = 2 * Math.PI * R;
    const center = RING / 2;
    return (
      <Box
        w="100%"
        py={compact ? "md" : "xl"}
        px={{ base: "xs", sm: "sm" }}
        style={{ minHeight: "100%" }}
      >
        <style>{`
          @keyframes zivo-ring-spin { to { transform: rotate(360deg); } }
          @keyframes zivo-ring-glow { 0%,100% { opacity: 0.35; transform: scale(0.94); } 50% { opacity: 0.55; transform: scale(1.04); } }
          @keyframes zivo-fade-up { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
          .zivo-load-copy { animation: zivo-fade-up 380ms cubic-bezier(0.32,0.72,0,1) both; }
          @media (prefers-reduced-motion: reduce) {
            .zivo-ring-spin, .zivo-ring-glow, .zivo-load-copy { animation: none !important; }
          }
        `}</style>
        <Paper
          withBorder
          shadow="paper"
          radius="xl"
          p={compact ? "lg" : "xl"}
          w="100%"
          maw={MCQ_CONTENT_MAX}
          mx="auto"
          bg="gray.0"
        >
          <Group
            align="flex-start"
            wrap={compact ? "wrap" : "nowrap"}
            gap={compact ? "lg" : "xl"}
          >
            {/* Progress ring sits beside the story — not stacked under a lone bulb icon. */}
            <Box
              w={compact ? "100%" : RING + 8}
              style={{
                flexShrink: 0,
                display: "flex",
                justifyContent: compact ? "center" : "flex-start",
              }}
            >
              <Box pos="relative" w={RING} h={RING} style={{ display: "grid", placeItems: "center" }}>
                <Box
                  className="zivo-ring-glow"
                  pos="absolute"
                  style={{
                    inset: -4,
                    borderRadius: "50%",
                    background:
                      "radial-gradient(circle, var(--mantine-color-lavender-3) 0%, transparent 68%)",
                    opacity: 0.45,
                  }}
                />
                <svg width={RING} height={RING} viewBox={`0 0 ${RING} ${RING}`} style={{ position: "relative" }}>
                  <defs>
                    <linearGradient id="zivo-ring-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                      <stop offset="0%" stopColor="var(--mantine-color-lavender-5)" />
                      <stop offset="55%" stopColor="var(--mantine-color-sage-5)" />
                      <stop offset="100%" stopColor="var(--mantine-color-sage-6)" />
                    </linearGradient>
                  </defs>
                  <circle
                    cx={center}
                    cy={center}
                    r={R}
                    fill="none"
                    stroke="var(--mantine-color-default-border)"
                    strokeOpacity={0.45}
                    strokeWidth={7}
                  />
                  {progressPct !== null ? (
                    <circle
                      cx={center}
                      cy={center}
                      r={R}
                      fill="none"
                      stroke="url(#zivo-ring-grad)"
                      strokeWidth={7}
                      strokeLinecap="round"
                      strokeDasharray={CIRC}
                      strokeDashoffset={CIRC * (1 - progressPct / 100)}
                      transform={`rotate(-90 ${center} ${center})`}
                      style={{ transition: "stroke-dashoffset 600ms cubic-bezier(0.32,0.72,0,1)" }}
                    />
                  ) : (
                    <circle
                      cx={center}
                      cy={center}
                      r={R}
                      fill="none"
                      stroke="url(#zivo-ring-grad)"
                      strokeWidth={7}
                      strokeLinecap="round"
                      strokeDasharray={`${CIRC * 0.22} ${CIRC * 0.78}`}
                      className="zivo-ring-spin"
                      style={{
                        animation: "zivo-ring-spin 1.1s linear infinite",
                        transformOrigin: "center",
                      }}
                    />
                  )}
                </svg>
                <Box pos="absolute" style={{ display: "grid", placeItems: "center" }}>
                  {progressPct !== null ? (
                    <Text
                      fz={compact ? 20 : 22}
                      fw={600}
                      c="var(--mantine-color-text)"
                      style={{
                        fontFamily: "var(--font-serif), Georgia, serif",
                        letterSpacing: "-0.02em",
                        lineHeight: 1,
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {progressPct}%
                    </Text>
                  ) : (
                    <Loader size={compact ? 24 : 28} color="lavender" type="oval" />
                  )}
                </Box>
              </Box>
            </Box>

            <Stack gap="md" style={{ flex: 1, minWidth: 0 }} align="stretch">
              <Group gap="sm" wrap="nowrap" align="center">
                <Box
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "4px 10px",
                    borderRadius: 999,
                    flexShrink: 0,
                    background: isDark
                      ? `var(--mantine-color-${accent}-1)`
                      : `var(--mantine-color-${accent}-0)`,
                    border: `1px solid var(--mantine-color-${accent}-${isDark ? 3 : 2})`,
                  }}
                >
                  {isTest ? (
                    <IconClipboardList size={13} stroke={2} style={{ color: `var(--mantine-color-${accent}-7)` }} />
                  ) : (
                    <IconBulb size={13} stroke={2} style={{ color: `var(--mantine-color-${accent}-7)` }} />
                  )}
                  <Text fz="xs" fw={600} c={`var(--mantine-color-${accent}-${isDark ? 8 : 7})`}>
                    {isTest ? "Test mode" : "Learn mode"}
                  </Text>
                </Box>
              </Group>

              <Stack key={waitStatus.rotateKey} className="zivo-load-copy" gap={6}>
                <Title
                  order={3}
                  fw={500}
                  style={{
                    letterSpacing: "-0.02em",
                    lineHeight: 1.2,
                    fontFamily: "var(--font-serif), Georgia, serif",
                  }}
                >
                  {waitStatus.title}
                </Title>
                <Text size="sm" c="dimmed" lh={1.55} maw={520}>
                  {waitStatus.detail}
                </Text>
              </Stack>

              {readingPhase ? (
                artifactStatus === "indexing" && (indexProgress ?? 0) > 0 ? (
                  <Stack gap={6} w="100%">
                    <Group justify="space-between" gap="xs">
                      <Text size="xs" c="dimmed" fw={600}>
                        Reading your source
                      </Text>
                      <Text size="xs" c="dimmed" ff="monospace">
                        {indexProgress ?? 0}%
                      </Text>
                    </Group>
                    <Progress value={indexProgress ?? 0} size="sm" radius="xl" color="lavender" animated />
                  </Stack>
                ) : queue?.rag_window_ready === false ? (
                  <Stack gap={6} w="100%">
                    <Text size="xs" c="dimmed" fw={600}>
                      Processing pages around your study material
                    </Text>
                    <Progress value={58} size="sm" radius="xl" color="lavender" animated />
                  </Stack>
                ) : null
              ) : null}

              {!newspaperReady ? (
                <GenerationStages
                  artifactStatus={artifactStatus}
                  indexProgress={indexProgress}
                  ragWindowReady={queue?.rag_window_ready}
                  pageTriageComplete={queue?.page_triage_complete}
                  generationPending={queue?.generation_pending}
                  questionsGenerated={generated}
                  questionBudget={budget}
                  compact={compact}
                  isDark={isDark}
                />
              ) : (
                <Text fz="sm" c="dimmed" lh={1.5}>
                  This edition was prepared ahead of time. Questions open as soon as they load.
                </Text>
              )}

              {stuckSeconds >= 45 && onRetry ? (
                <Stack gap={6}>
                  <Text size="sm" c="var(--mantine-color-text)">
                    Something&apos;s taking a while.
                  </Text>
                  <Button variant="default" color="lavender" size="compact-sm" radius="xl" onClick={onRetry}>
                    Retry
                  </Button>
                </Stack>
              ) : null}

              {!compact && !isTest && catEnabled ? (
                <Box w="100%" maw={360} style={{ height: 72 }}>
                  <PetPlayground count={1} species="cat" wander height={72} style={{ width: "100%" }} />
                </Box>
              ) : null}
            </Stack>
          </Group>
        </Paper>
      </Box>
    );
  }

  return (
    <Stack key={displayStem} h="100%" gap={0} align="stretch" miw={0} style={{ overflow: "hidden", minWidth: 0 }}>
      <style>{`
        @keyframes mcq-rise {
          from { opacity: 0; transform: translateY(14px) scale(0.99); filter: blur(4px); }
          to { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
        }
        /* Premium "focus-pull" entrance on every question swap. Pure CSS keyframes -
           reliable across SSR/strict-mode (framer AnimatePresence stalls here). The
           title leads; options cascade in via per-item animation-delay below. */
        .mcq-q { animation: mcq-rise 460ms cubic-bezier(0.32,0.72,0,1) both; }
        .mcq-opt {
          animation: mcq-rise 460ms cubic-bezier(0.32,0.72,0,1) both;
          transition: transform 160ms cubic-bezier(0.32,0.72,0,1), border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
        }
        .mcq-opt:not(:disabled):hover { transform: translateY(-2px); box-shadow: var(--mantine-shadow-paper); border-color: var(--mantine-color-lavender-4) !important; }
        .mcq-opt:not(:disabled):active { transform: translateY(0); }
        /* UnstyledButton strips the default ring, so arrow-key focus would be
           invisible - which matters most for select-all, where moving focus is the
           only feedback until you toggle. */
        .mcq-opt:focus-visible { outline: 2px solid var(--mantine-color-lavender-5); outline-offset: 2px; }
        /* Checking: the chosen option breathes while the grade comes back. */
        @keyframes mcq-check-pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(124, 109, 242, 0.0); }
          50% { box-shadow: 0 0 0 4px rgba(124, 109, 242, 0.22); }
        }
        .mcq-opt-checking { animation: mcq-check-pulse 1.05s ease-in-out infinite !important; }
        @media (prefers-reduced-motion: reduce) {
          .mcq-q, .mcq-opt, .mcq-opt-checking { animation: none !important; }
        }
      `}</style>

      {/* Centered, scrollable content region. The card height is fixed by the
          parent (clamp), so showing feedback or a longer stem reflows WITHIN
          this region instead of resizing the card - the footer below never
          moves and the page no longer jumps. ScrollHintArea hides the scrollbar
          and shows a fade + chevron when there's more below. */}
      <ScrollHintArea>
      {/* Top-anchored so the question is its own scrollable page. */}
      <Box
        maw={MCQ_CONTENT_MAX}
        w="100%"
        miw={0}
        mx="auto"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: compact ? 14 : 20,
        }}
      >
      {/* Stem, options, and feedback scroll together inside ScrollHintArea. */}
      <Box
        style={{
          position: "relative",
          flexShrink: 0,
          display: "flex",
          flexDirection: "column",
          paddingBottom: 6,
        }}
      >
      {onFlagQuestion && !isTest ? (
        <Group justify="flex-end" mb={4} style={{ position: "absolute", top: 0, right: 0, zIndex: 4 }}>
          <Menu shadow="paper" width={220} position="bottom-end" withinPortal>
            <Menu.Target>
              <ActionIcon
                variant="subtle"
                color={flagged ? "terracotta" : "gray"}
                radius="xl"
                size="sm"
                loading={flagBusy}
                disabled={flagged || flagBusy}
                aria-label={flagged ? "Question flagged" : "Flag this question"}
              >
                <IconFlag size={14} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>Something wrong with this question?</Menu.Label>
              <Menu.Item onClick={() => onFlagQuestion("ambiguous")}>Ambiguous / unclear</Menu.Item>
              <Menu.Item onClick={() => onFlagQuestion("wrong_key")}>Wrong answer key</Menu.Item>
              <Menu.Item onClick={() => onFlagQuestion("not_grounded")}>Not in the source</Menu.Item>
              <Menu.Item onClick={() => onFlagQuestion("other")}>Other</Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
      ) : null}
      <Title
        order={2}
        className="mcq-q"
        fw={500}
        fz={compact ? MCQ_STEM_FONT_SIZE.compact : MCQ_STEM_FONT_SIZE.default}
        lh={statementStem ? 1.55 : 1.4}
        ta={statementStem ? "left" : "center"}
        c="var(--mantine-color-text)"
        style={{
          fontFamily: "var(--font-serif), Georgia, serif",
          letterSpacing: "-0.01em",
          maxWidth: statementStem ? MCQ_CONTENT_MAX : "100%",
          marginInline: "auto",
          overflowWrap: "break-word",
          wordBreak: "normal",
          whiteSpace: "pre-line",
          paddingInline: onFlagQuestion && !isTest ? 28 : 0,
        }}
      >
        {displayStem}
      </Title>
      </Box>

      {multiSelect && !graded ? (
        <Text fz="xs" fw={600} c="dimmed" ta="center" tt="uppercase" style={{ letterSpacing: "0.06em", flexShrink: 0 }}>
          Select all that apply
        </Text>
      ) : null}
      <Stack gap={compact ? 8 : 10} mih={0} style={{ flexShrink: 0 }}>
        {safeOptions.map((opt, i) => {
          const value = String(i);
          const isSelected = multiSelect ? multiChosen.includes(i) : selected === value;
          // Test mode never reveals correctness per-question - the chosen option just
          // shows as "answered" (its selected tint), graded silently for the end.
          const correctSet = gradeState?.correctIndices;
          const isCorrectOption = reveal && (
            multiSelect && correctSet ? correctSet.includes(i) : gradeState.correctIndex === i
          );
          const isWrongSelected = reveal && !gradeState.correct && isSelected && !isCorrectOption;
          const { border, background, chipBg, chipColor, borderWidth } = mcqOptionChrome(isDark, {
            isSelected,
            isCorrectOption,
            isWrongSelected,
          });
          const dim = optionsLocked && !isCorrectOption && !isWrongSelected;
          const isChecking = checking && isSelected;
          const checkingDim = checking && !isSelected;
          return (
            <UnstyledButton
              key={value}
              className={isChecking ? "mcq-opt mcq-opt-checking" : "mcq-opt"}
              // Arrow-key navigation targets these by attribute. Only the interactive
              // panel carries it - the read-only review view renders its own options.
              data-mcq-option={i}
              // Keep clickable when locked: disabled buttons swallow clicks, and after
              // a correct answer the focused option is exactly where the learner taps
              // again / presses Enter. Treat that as Continue.
              disabled={checking}
              aria-disabled={optionsLocked || checking}
              onClick={() => {
                if (checking) return;
                if (optionsLocked) {
                  onContinue();
                  return;
                }
                if (multiSelect) { onToggle?.(i); return; }
                // Second click on the already-selected option checks it - the
                // answer IS the button, so there's no reach for the far one.
                if (isSelected && !submitting) { onSubmit(); return; }
                onSelect(value);
              }}
              style={{
                animationDelay: `${90 + i * 60}ms`,
                width: "100%",
                borderRadius: 14,
                padding: compact ? "12px 12px" : "14px 16px",
                minHeight: 48,
                border: `${borderWidth}px solid ${border}`,
                background,
                opacity: checkingDim ? 0.42 : dim ? 0.6 : 1,
                transition: "opacity 280ms cubic-bezier(0.32,0.72,0,1)",
              }}
            >
              <Group wrap="nowrap" align="center" gap={compact ? "sm" : "md"}>
                <Box
                  style={{
                    flexShrink: 0,
                    width: 26,
                    height: 26,
                    borderRadius: 8,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: chipBg,
                    color: chipColor,
                    fontFamily: "var(--font-sans), sans-serif",
                    fontWeight: 700,
                    fontSize: 13,
                    transition: "background 160ms ease, color 160ms ease",
                  }}
                >
                  {isCorrectOption ? (
                    <IconCheck size={15} stroke={2.4} />
                  ) : isWrongSelected ? (
                    <IconX size={15} stroke={2.4} />
                  ) : multiSelect && isSelected ? (
                    <IconCheck size={15} stroke={2.4} />
                  ) : (
                    String.fromCharCode(65 + i)
                  )}
                </Box>
                <Text
                  lh={1.45}
                  ta="left"
                  fz={compact ? MCQ_OPTION_FONT_SIZE.compact : MCQ_OPTION_FONT_SIZE.default}
                  c="var(--mantine-color-text)"
                  style={{
                    flex: 1,
                    minWidth: 0,
                    overflowWrap: "break-word",
                  }}
                >
                  {opt}
                </Text>
                {isSelected && !multiSelect && !graded && !checking && (
                  <Box
                    aria-hidden
                    style={{
                      flexShrink: 0,
                      display: compact ? "none" : "flex",
                      alignItems: "center",
                      gap: 3,
                      padding: "3px 9px",
                      borderRadius: 999,
                      background: "var(--mantine-color-lavender-1)",
                      color: "var(--mantine-color-lavender-7)",
                      fontSize: 12,
                      fontWeight: 600,
                      whiteSpace: "nowrap",
                    }}
                  >
                    Check
                    <IconArrowRight size={13} stroke={2.4} />
                  </Box>
                )}
              </Group>
            </UnstyledButton>
          );
        })}
      </Stack>

      {reveal && feedback ? (
        // Click the feedback (or press Enter / Space) to continue - the target is
        // right where your eyes already are, no reach for the bottom button. A
        // text-selection guard means highlighting a phrase never advances.
        <Box
          onClick={() => {
            if (!window.getSelection()?.toString()) onContinue();
          }}
          style={{ cursor: "pointer" }}
        >
          <McqFeedbackCard
            feedback={feedback}
            isCorrect={gradeState?.correct === true}
            compact={compact}
            isDark={isDark}
          />
          {mode === "learn" && !gradeState?.correct && focusConcept && onFocusConcept ? (
            <Group justify="center" mt={compact ? 12 : 16}>
              <Button
                variant="light"
                color="lavender"
                radius="xl"
                size="compact-sm"
                leftSection={<IconTarget size={14} stroke={2} />}
                onClick={() => onFocusConcept(focusConcept)}
              >
                Focus on “{focusConcept}”
              </Button>
            </Group>
          ) : null}
          <Text fz="xs" c="dimmed" ta="center" mt={compact ? 6 : 8} fw={500}>
            Click anywhere, or press Enter, to continue →
          </Text>
        </Box>
      ) : reveal && !isTest ? (
        // Verdict already shown; coaching still streaming. A rotating status (no dots
        // spinner) - the persistent roaming cat below stays put (no respawn per turn).
        <FeedbackWritingStatus compact={compact} />
      ) : graded && isTest ? (
        <Group justify="center" gap={8} mt={compact ? 8 : 12} style={{ flexShrink: 0 }}>
          <ThemeIcon size={22} radius="xl" variant="light" color="forest">
            <IconCheck size={13} stroke={2.4} />
          </ThemeIcon>
          <Text fz="sm" c="dimmed" fw={500}>
            Answer recorded - you&rsquo;ll see your score at the end
          </Text>
        </Group>
      ) : checking ? (
        <McqCheckingStatus compact={compact} />
      ) : null}
      </Box>

      {/* Roaming cat while idle or checking — keeps the card alive, no spinner chrome. */}
      {catEnabled && !isTest && !compact && !graded ? (
        <Box style={{ flex: 1, minHeight: 150, width: "100%" }}>
          <PetPlayground count={1} species="cat" wander height="100%" style={{ width: "100%" }} />
        </Box>
      ) : null}

      {/* Mode strip when idle — hide during check/grade so the question stays the hero. */}
      {!compact && !graded && !checking ? (
      <Center style={{ marginTop: "auto", paddingTop: compact ? 14 : 22, flexShrink: 0 }}>
        <Box
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 9,
            maxWidth: "100%",
            padding: compact ? "6px 12px" : "7px 16px",
            borderRadius: 999,
            background: isDark ? `var(--mantine-color-${accent}-1)` : `var(--mantine-color-${accent}-0)`,
            border: `1px solid var(--mantine-color-${accent}-${isDark ? 3 : 2})`,
          }}
        >
          {isTest ? <IconClipboardList size={14} stroke={2} style={{ flexShrink: 0, color: `var(--mantine-color-${accent}-${isDark ? 8 : 7})` }} /> : <IconBulb size={14} stroke={2} style={{ flexShrink: 0, color: `var(--mantine-color-${accent}-${isDark ? 8 : 7})` }} />}
          <Text
            fz="xs"
            fw={600}
            c={`var(--mantine-color-${accent}-${isDark ? 9 : 8})`}
            style={{ letterSpacing: "-0.01em", overflowWrap: "anywhere", lineHeight: 1.35 }}
          >
            {isTest
              ? compact
                ? `Test · graded at end${
                    isNewspaper
                      ? (queue?.edition_question_total ?? queue?.questions_generated ?? 0) > 0
                        ? ` · ${queue?.questions_answered ?? 0}/${queue?.edition_question_total ?? queue?.questions_generated ?? 0}`
                        : ""
                      : (queue?.question_budget ?? 0) > 0
                        ? ` · ${queue?.questions_answered ?? 0}/${queue?.question_budget}`
                        : ""
                  }`
                : `Test · graded at the end${
                    isNewspaper
                      ? (queue?.edition_question_total ?? queue?.questions_generated ?? 0) > 0
                        ? ` · ${queue?.questions_answered ?? 0} of ${queue?.edition_question_total ?? queue?.questions_generated ?? 0} answered`
                        : ""
                      : (queue?.question_budget ?? 0) > 0
                        ? ` · ${queue?.questions_answered ?? 0} of ${queue?.question_budget} answered`
                        : ""
                  }`
              : compact
                ? "Learn · feedback after each answer"
                : "Learn · instant feedback after each answer, retry until it clicks"}
          </Text>
        </Box>
      </Center>
      ) : null}
      </ScrollHintArea>

      {!checking ? (
      <Stack align="center" gap={8} pt={compact ? "sm" : "md"} style={{ flexShrink: 0 }}>
        {!graded && hasSelection && mode === "learn" && onConfidenceChange ? (
          <Stack gap={4} align="center" style={{ width: "100%", maxWidth: 340 }}>
            <Text size="xs" c="dimmed" fw={500} tt="uppercase" style={{ letterSpacing: "0.05em" }}>
              How sure are you?
            </Text>
            <SegmentedControl
              size="xs"
              radius="xl"
              value={confidence == null ? "1" : String(confidence)}
              onChange={(v) => onConfidenceChange(Number(v))}
              data={[
                { label: "Guess", value: "0" },
                { label: "Unsure", value: "1" },
                { label: "Confident", value: "2" },
              ]}
            />
          </Stack>
        ) : null}
        {showNextQuestion ? (
          <Button
            radius="xl"
            size="md"
            color={isTest ? "forest" : "sage"}
            maw={compact ? "100%" : 300}
            w="100%"
            loading={submitting}
            onClick={onContinue}
            rightSection={<IconArrowRight size={18} stroke={2} />}
          >
            {isTest ? "Next" : "Next question"}
          </Button>
        ) : (
          <Button
            radius="xl"
            size="md"
            color={accent}
            maw={compact ? "100%" : 300}
            w="100%"
            onClick={onSubmit}
            disabled={!hasSelection}
          >
            {isTest ? "Submit answer" : "Check answer"}
          </Button>
        )}
        {onAdvance && (
          <Button radius="xl" size="sm" variant="subtle" onClick={onAdvance}>
            Next page
          </Button>
        )}
        {canReview && onReviewPrevious ? (
          <Button
            variant="subtle"
            color="gray"
            size="compact-sm"
            radius="xl"
            leftSection={<IconHistory size={15} stroke={1.7} />}
            onClick={onReviewPrevious}
          >
            {isNewspaper ? "Previous question" : "Review previous"}
          </Button>
        ) : !compact ? (
          <Text size="xs" c="dimmed" ta="center" style={{ opacity: 0.85 }}>
            {showNextQuestion
              ? "Press Enter for the next question"
              : "Press A-D or arrows to choose · Enter to check"}
          </Text>
        ) : null}
      </Stack>
      ) : null}
    </Stack>
  );
}

/**
 * Read-only review of a previously-answered question. Mirrors McqHeroPanel's calm
 * layout (serif stem, the same option chrome + feedback card) but locks everything
 * and adds step controls so the learner can flip back through what they answered.
 */
export function McqReviewView({
  card,
  index,
  total,
  compact = false,
  onPrev,
  onNext,
  onExit,
}: {
  card: AnsweredCard;
  index: number;
  total: number;
  compact?: boolean;
  onPrev?: () => void;
  onNext: () => void;
  onExit: () => void;
}) {
  const isDark = useIsDark();
  const displayStem = formatMcqStemForDisplay(card.stem);
  const statementStem = isStatementStyleStem(displayStem);
  const safeOptions = normalizeMcqOptions(card.options);
  const { correct, correctIndex, correctIndices } = card.gradeState;
  const isCorrect = (i: number) =>
    correctIndices && correctIndices.length >= 2 ? correctIndices.includes(i) : correctIndex === i;

  return (
    <Stack key={card.assertionId} h="100%" gap={0} align="stretch" style={{ overflow: "hidden" }}>
      <Group
        justify="space-between"
        align="center"
        wrap="wrap"
        px={4}
        pb={8}
        gap="xs"
        style={{ flexShrink: 0 }}
      >
        <Group gap={8} wrap="nowrap" align="center" style={{ minWidth: 0 }}>
          <ThemeIcon variant="light" color="lavender" radius="xl" size={26}>
            <IconHistory size={15} stroke={1.8} />
          </ThemeIcon>
          <Text fz="sm" fw={600} c="var(--mantine-color-text)" lineClamp={1}>
            Reviewing
            <Text component="span" inherit c="dimmed" fw={500}>
              {"  "}
              {index + 1} of {total}
            </Text>
          </Text>
        </Group>
        <Button
          variant="light"
          color="lavender"
          size="compact-sm"
          radius="xl"
          onClick={onExit}
          rightSection={<IconArrowRight size={15} stroke={2} />}
        >
          Back to question
        </Button>
      </Group>

      <ScrollHintArea>
        <Box
          maw={MCQ_CONTENT_MAX}
          w="100%"
          mx="auto"
          style={{ display: "flex", flexDirection: "column", gap: compact ? 14 : 18 }}
        >
          <Title
            order={2}
            fw={500}
            fz={compact ? MCQ_STEM_FONT_SIZE.compact : MCQ_STEM_FONT_SIZE.default}
            lh={statementStem ? 1.55 : 1.4}
            ta={statementStem ? "left" : "center"}
            c="var(--mantine-color-text)"
            style={{
              fontFamily: "var(--font-serif), Georgia, serif",
              letterSpacing: "-0.01em",
              maxWidth: "100%",
              marginInline: "auto",
              overflowWrap: "break-word",
              wordBreak: "normal",
              whiteSpace: "pre-line",
            }}
          >
            {displayStem}
          </Title>

          <Stack gap={compact ? 8 : 10} mih={0} style={{ flexShrink: 0 }}>
            {safeOptions.map((opt, i) => {
              const isCorrectOption = isCorrect(i);
              const chosen =
                Array.isArray(card.selectedIndices) && card.selectedIndices.length > 0
                  ? card.selectedIndices.includes(i)
                  : card.selectedIndex === i;
              const isWrongSelected = !correct && chosen && !isCorrectOption;
              const { border, background, chipBg, chipColor, borderWidth } = mcqOptionChrome(isDark, {
                isSelected: chosen,
                isCorrectOption,
                isWrongSelected,
              });
              const dim = !isCorrectOption && !isWrongSelected;
              return (
                <Box
                  key={i}
                  style={{
                    width: "100%",
                    borderRadius: 14,
                    padding: compact ? "12px 12px" : "14px 16px",
                    minHeight: 48,
                    border: `${borderWidth}px solid ${border}`,
                    background,
                    opacity: dim ? 0.6 : 1,
                  }}
                >
                  <Group wrap="nowrap" align="center" gap={compact ? "sm" : "md"}>
                    <Box
                      style={{
                        flexShrink: 0,
                        width: 26,
                        height: 26,
                        borderRadius: 8,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: chipBg,
                        color: chipColor,
                        fontFamily: "var(--font-sans), sans-serif",
                        fontWeight: 700,
                        fontSize: 13,
                      }}
                    >
                      {isCorrectOption ? (
                        <IconCheck size={15} stroke={2.4} />
                      ) : isWrongSelected ? (
                        <IconX size={15} stroke={2.4} />
                      ) : (
                        String.fromCharCode(65 + i)
                      )}
                    </Box>
                    <Text
                      lh={1.45}
                      ta="left"
                      fz={compact ? MCQ_OPTION_FONT_SIZE.compact : MCQ_OPTION_FONT_SIZE.default}
                      c="var(--mantine-color-text)"
                      style={{
                        flex: 1,
                        minWidth: 0,
                        overflowWrap: "break-word",
                      }}
                    >
                      {opt}
                    </Text>
                  </Group>
                </Box>
              );
            })}
          </Stack>

          {card.feedback ? (
            <McqFeedbackCard feedback={card.feedback} isCorrect={correct} compact={compact} isDark={isDark} />
          ) : null}
        </Box>
      </ScrollHintArea>

      <Group justify="center" gap={8} pt={compact ? "sm" : "md"} style={{ flexShrink: 0 }}>
        <Button
          variant="default"
          radius="xl"
          size="sm"
          leftSection={<IconArrowLeft size={16} stroke={2} />}
          onClick={onPrev}
          disabled={!onPrev}
        >
          Previous
        </Button>
        <Button
          variant="default"
          radius="xl"
          size="sm"
          rightSection={<IconArrowRight size={16} stroke={2} />}
          onClick={onNext}
        >
          {index + 1 < total ? "Next" : "Back to question"}
        </Button>
      </Group>
    </Stack>
  );
}
