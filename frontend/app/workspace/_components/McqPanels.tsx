"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ActionIcon,
  Box,
  Button,
  Center,
  Group,
  Menu,
  Paper,
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
  IconX,
} from "@tabler/icons-react";
import { GenerationStages } from "@/app/workspace/_components/GenerationStages";
import { PetPlayground } from "@/app/_components/pets/PetPlayground";
import { CAT_ENABLED_KEY } from "@/app/_components/pets/PetPlayground";
import { mcqOptionChrome, McqFeedbackCard } from "@/app/_components/mcq/McqCard";
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

function FeedbackWritingStatus({ compact }: { compact?: boolean }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = window.setInterval(
      () => setI((v) => (v + 1) % FEEDBACK_WRITING_MESSAGES.length),
      1300,
    );
    return () => window.clearInterval(id);
  }, []);
  return (
    <Group justify="center" mt={compact ? 8 : 12} style={{ flexShrink: 0, minHeight: 22 }}>
      <style>{`
        @keyframes zv-fb-rotate { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }
        .zv-fb-rotate { animation: zv-fb-rotate 320ms cubic-bezier(0.32,0.72,0,1) both; }
        @media (prefers-reduced-motion: reduce) { .zv-fb-rotate { animation: none !important; } }
      `}</style>
      <Text key={i} className="zv-fb-rotate" fz="sm" c="dimmed" fw={500}>
        {FEEDBACK_WRITING_MESSAGES[i]}
      </Text>
    </Group>
  );
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
}) {
  const isDark = useIsDark();
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
  const waiting =
    mcqLoading ||
    artifactStatus === "indexing" ||
    !hasQuestion ||
    (Boolean(queue?.generation_pending) && !queue?.current_assertion_id);

  const [statusTick, setStatusTick] = useState(0);
  const stagnant =
    waiting && Boolean(queue?.generation_pending) && (queue?.questions_generated ?? 0) === 0;
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
      questionBudget: queue?.question_budget,
      poolAvailable: queue?.pool_available,
    },
    statusTick,
  );
  // autoInvoke - without it Mantine's useInterval never starts, so the wait-status
  // copy never rotated. The `if (waiting)` guard keeps it a no-op while idle.
  useInterval(
    () => {
      if (waiting) setStatusTick((t) => t + 1);
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
      if (waiting) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      // Enter OR Space advances / checks - whichever hand is on the keyboard, no
      // reach for the mouse. A focused button/link keeps its native activation so
      // we never double-fire.
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        // Arrowing leaves focus ON an option, so this is now the common case. Drive
        // the option's own click handler and preventDefault to suppress the native
        // activation, rather than relying on that native activation to fire at all -
        // one click either way, and Enter behaves the same however you got here.
        const focusedOption = (document.activeElement as HTMLElement | null)?.closest<HTMLButtonElement>(
          "[data-mcq-option]",
        );
        if (focusedOption) {
          if (optionsLocked || checking) return;
          e.preventDefault();
          focusedOption.click();
          return;
        }
        if (tag === "BUTTON" || tag === "A") return;
        if (graded) {
          e.preventDefault();
          onContinue();
        } else if (hasSelection && !submitting) {
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
  }, [waiting, graded, hasSelection, submitting, optionsLocked, checking, selected, safeOptions.length, multiSelect, onSelect, onToggle, onSubmit, onContinue]);

  if (waiting) {
    // Determinate progress during generation: turn the vague spinner into a
    // moving bar the user can watch fill toward the page's question budget.
    // Known waits feel ~30% shorter than unknown waits (HCI research). Falls
    // back to an animated indeterminate bar when no budget is known yet.
    const generated = queue?.questions_generated ?? 0;
    const budget = queue?.question_budget ?? 0;
    // Stage-weighted overall progress, driven by live backend signals, so the ring
    // always reflects real pipeline movement (read -> plan -> write) instead of
    // sitting at 0% until the first question lands.
    const readingPhase = artifactStatus === "indexing" || queue?.rag_window_ready === false;
    const planningPhase = !readingPhase && !queue?.page_triage_complete;
    const progressPct = readingPhase
      ? Math.min(28, Math.round((indexProgress ?? 0) * 0.28))
      : planningPhase
        ? 40
        : budget > 0
          ? Math.min(100, 48 + Math.round((generated / budget) * 52))
          : 52;
    const RING = compact ? 124 : 140;
    const R = RING / 2 - 12;
    const CIRC = 2 * Math.PI * R;
    const center = RING / 2;
    return (
      <Center h="100%" px="sm" py={compact ? "md" : "lg"}>
        <style>{`
          @keyframes zivo-ring-spin { to { transform: rotate(360deg); } }
          @keyframes zivo-blob-a { 0%,100% { transform: translate(0,0) scale(1); } 50% { transform: translate(9px,-11px) scale(1.16); } }
          @keyframes zivo-blob-b { 0%,100% { transform: translate(0,0) scale(1.06); } 50% { transform: translate(-11px,9px) scale(0.9); } }
          @keyframes zivo-blob-c { 0%,100% { transform: translate(0,0) scale(0.95); } 50% { transform: translate(7px,11px) scale(1.12); } }
          @keyframes zivo-fade-up { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
          @keyframes zivo-bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
          .zivo-load-copy { animation: zivo-fade-up 380ms cubic-bezier(0.32,0.72,0,1) both; }
          @media (prefers-reduced-motion: reduce) {
            .zivo-blob, .zivo-ring-spin, .zivo-bob, .zivo-load-copy { animation: none !important; }
          }
        `}</style>
        <Paper
          withBorder
          radius="xl"
          p={compact ? "lg" : "xl"}
          w="100%"
          maw={compact ? 380 : 440}
          style={{ background: "var(--mantine-color-body)" }}
        >
        <Stack align="center" gap={compact ? "md" : "lg"}>
          <Box pos="relative" w={RING} h={RING} style={{ display: "grid", placeItems: "center" }}>
            {/* Colorful aurora - three soft brand-tinted blobs drifting behind the ring */}
            <Box className="zivo-blob" pos="absolute" style={{ inset: -6, borderRadius: "50%", filter: "blur(22px)", background: "radial-gradient(60% 60% at 30% 30%, var(--mantine-color-lavender-4), transparent 70%)", opacity: 0.55, animation: "zivo-blob-a 4.5s ease-in-out infinite" }} />
            <Box className="zivo-blob" pos="absolute" style={{ inset: -6, borderRadius: "50%", filter: "blur(22px)", background: "radial-gradient(55% 55% at 72% 42%, var(--mantine-color-sage-4), transparent 70%)", opacity: 0.5, animation: "zivo-blob-b 5.4s ease-in-out infinite" }} />
            <Box className="zivo-blob" pos="absolute" style={{ inset: -6, borderRadius: "50%", filter: "blur(22px)", background: "radial-gradient(55% 55% at 50% 76%, var(--mantine-color-terracotta-3), transparent 70%)", opacity: 0.45, animation: "zivo-blob-c 5s ease-in-out infinite" }} />
            <svg width={RING} height={RING} viewBox={`0 0 ${RING} ${RING}`} style={{ position: "relative" }}>
              <defs>
                <linearGradient id="zivo-ring-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="var(--mantine-color-lavender-5)" />
                  <stop offset="50%" stopColor="var(--mantine-color-sage-5)" />
                  <stop offset="100%" stopColor="var(--mantine-color-terracotta-5)" />
                </linearGradient>
              </defs>
              <circle cx={center} cy={center} r={R} fill="none" stroke="var(--mantine-color-default-border)" strokeOpacity={0.5} strokeWidth={8} />
              <circle
                cx={center}
                cy={center}
                r={R}
                fill="none"
                stroke="url(#zivo-ring-grad)"
                strokeWidth={8}
                strokeLinecap="round"
                strokeDasharray={CIRC}
                strokeDashoffset={CIRC * (1 - progressPct / 100)}
                transform={`rotate(-90 ${center} ${center})`}
                style={{ transition: "stroke-dashoffset 600ms cubic-bezier(0.32,0.72,0,1)" }}
              />
            </svg>
            <Box pos="absolute" style={{ display: "grid", placeItems: "center" }}>
              <Text
                fz={compact ? 22 : 26}
                fw={600}
                c="var(--mantine-color-text)"
                style={{ fontFamily: "var(--font-serif), Georgia, serif", letterSpacing: "-0.02em", lineHeight: 1 }}
              >
                {progressPct}%
              </Text>
            </Box>
          </Box>

          <Stack key={waitStatus.rotateKey} className="zivo-load-copy" gap={4} align="center">
            <Text
              fz={compact ? "md" : "lg"}
              fw={600}
              ta="center"
              c="var(--mantine-color-text)"
              style={{ letterSpacing: "-0.02em", fontFamily: "var(--font-serif), Georgia, serif" }}
            >
              {waitStatus.title}
            </Text>
            <Text size="sm" c="dimmed" ta="center" lh={1.55} maw={290}>
              {waitStatus.detail}
            </Text>
          </Stack>

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

          {stuckSeconds >= 45 && onRetry ? (
            <Stack gap={6} align="center">
              <Text size="sm" c="var(--mantine-color-text)" ta="center">
                Something&apos;s taking a while.
              </Text>
              <Button variant="default" color="lavender" size="compact-sm" onClick={onRetry}>
                Retry generation
              </Button>
            </Stack>
          ) : null}
        </Stack>
        </Paper>
      </Center>
    );
  }

  return (
    <Stack key={stem} h="100%" gap={0} align="stretch" style={{ overflow: "hidden" }}>
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
        @keyframes mcq-check-dots { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }
        .mcq-check-dot { animation: mcq-check-dots 1.2s ease-in-out infinite; }
        @media (prefers-reduced-motion: reduce) {
          .mcq-q, .mcq-opt, .mcq-opt-checking, .mcq-check-dot { animation: none !important; }
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
        style={{
          width: "100%",
          display: "flex",
          flexDirection: "column",
          gap: compact ? 14 : 20,
        }}
      >
      {/* The stem stays put (sticky) while the options + explanation scroll under it. */}
      <Box
        style={{
          position: "sticky",
          top: 0,
          zIndex: 3,
          flexShrink: 0,
          minHeight: compact ? 56 : 72,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          paddingBottom: 6,
          background: "var(--mantine-color-body)",
        }}
      >
      {onFlagQuestion && !isTest ? (
        <Group justify="flex-end" mb={4} style={{ position: "absolute", top: 0, right: 0 }}>
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
        lh={1.3}
        ta="center"
        c="var(--mantine-color-text)"
        style={{
          fontFamily: "var(--font-serif), Georgia, serif",
          fontSize: compact ? "clamp(1rem, 4.4vw, 1.3rem)" : "clamp(1.2rem, 2.2vw, 1.9rem)",
          letterSpacing: "-0.01em",
          maxWidth: "min(640px, 100%)",
          marginInline: "auto",
          overflowWrap: "anywhere",
          whiteSpace: "pre-line", // statement/matching/code stems arrive with \n line breaks
        }}
      >
        {stem}
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
          return (
            <UnstyledButton
              key={value}
              className={isChecking ? "mcq-opt mcq-opt-checking" : "mcq-opt"}
              // Arrow-key navigation targets these by attribute. Only the interactive
              // panel carries it - the read-only review view renders its own options.
              data-mcq-option={i}
              disabled={optionsLocked || checking}
              onClick={() => {
                if (optionsLocked || checking) return;
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
                  size={compact ? "sm" : "md"}
                  lh={1.45}
                  ta="left"
                  style={{ flex: 1, fontSize: compact ? undefined : "1.0625rem", color: "var(--mantine-color-text)" }}
                >
                  {opt}
                </Text>
                {isSelected && !multiSelect && !graded && !checking && (
                  <Box
                    aria-hidden
                    style={{
                      flexShrink: 0,
                      display: "flex",
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
      ) : null}
      </Box>

      {/* One cat roams the whole empty area below the question, wandering in 2D
          (not one straight line). It stays MOUNTED across the answer→next cycle
          (the gate no longer depends on `graded`), so the same cat persists and
          keeps roaming from where it was instead of respawning every turn.
          Desktop Learn only - that's where the free space is. */}
      {catEnabled && !isTest && !compact && (
        <Box style={{ flex: 1, minHeight: 150, width: "100%" }}>
          <PetPlayground count={1} species="cat" wander height="100%" style={{ width: "100%" }} />
        </Box>
      )}

      {/* Anchored to the bottom of the question area - turns the old dead space into a
          calm, mode-defining strip (and the live test tally). */}
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
          <Text fz="xs" fw={600} c={`var(--mantine-color-${accent}-${isDark ? 9 : 8})`} style={{ letterSpacing: "-0.01em" }}>
            {isTest
              ? `Test · graded at the end${(queue?.question_budget ?? 0) > 0 ? ` · ${queue?.questions_answered ?? 0} of ${queue?.question_budget} answered` : ""}`
              : "Learn · instant feedback after each answer, retry until it clicks"}
          </Text>
        </Box>
      </Center>
      </ScrollHintArea>

      <Stack align="center" gap={8} pt={compact ? "sm" : "md"} style={{ flexShrink: 0 }}>
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
            loading={checking}
            disabled={!hasSelection}
          >
            {checking ? "Checking…" : isTest ? "Submit answer" : "Check answer"}
          </Button>
        )}
        {onAdvance && (
          <Button radius="xl" size="sm" variant="subtle" onClick={onAdvance}>
            Next page
          </Button>
        )}
        {checking ? (
          <Text size="xs" c="dimmed" ta="center" style={{ opacity: 0.9 }}>
            Checking your answer
            <Text component="span" inherit className="mcq-check-dot">…</Text>
          </Text>
        ) : canReview && onReviewPrevious ? (
          <Button
            variant="subtle"
            color="gray"
            size="compact-sm"
            radius="xl"
            leftSection={<IconHistory size={15} stroke={1.7} />}
            onClick={onReviewPrevious}
          >
            Review previous
          </Button>
        ) : !compact ? (
          <Text size="xs" c="dimmed" ta="center" style={{ opacity: 0.85 }}>
            {showNextQuestion
              ? "Press Enter for the next question"
              : "Press A-D or arrows to choose · Enter to check"}
          </Text>
        ) : null}
      </Stack>
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
        <Box style={{ width: "100%", display: "flex", flexDirection: "column", gap: compact ? 14 : 18 }}>
          <Title
            order={2}
            fw={500}
            lh={1.3}
            ta="center"
            c="var(--mantine-color-text)"
            style={{
              fontFamily: "var(--font-serif), Georgia, serif",
              fontSize: compact ? "clamp(1rem, 4.4vw, 1.3rem)" : "clamp(1.2rem, 2.2vw, 1.9rem)",
              letterSpacing: "-0.01em",
              maxWidth: "min(640px, 100%)",
              marginInline: "auto",
              overflowWrap: "anywhere",
              whiteSpace: "pre-line", // statement/matching/code stems arrive with \n line breaks
            }}
          >
            {card.stem}
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
                      size={compact ? "sm" : "md"}
                      lh={1.45}
                      ta="left"
                      style={{ flex: 1, fontSize: compact ? undefined : "1.0625rem", color: "var(--mantine-color-text)" }}
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
