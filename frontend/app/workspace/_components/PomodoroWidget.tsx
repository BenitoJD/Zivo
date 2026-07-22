"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { ActionIcon, Box, Group, Text, Tooltip, UnstyledButton } from "@mantine/core";
import {
  IconChevronRight,
  IconClockHour4,
  IconPlayerPause,
  IconPlayerPlay,
  IconPlayerSkipForward,
  IconRotateClockwise,
} from "@tabler/icons-react";

/**
 * A calm, self-contained Pomodoro focus timer for study sessions. Floating,
 * collapsible, and independent of the rest of the study view. Auto-cycles
 * focus → break → focus (a longer break every 4th focus), plays a gentle
 * generated chime at each phase change (no audio file / no copyright), and
 * survives navigation via localStorage.
 */

type Phase = "focus" | "break" | "long";
type Preset = { label: string; focus: number; break: number; long: number };

const PRESETS: Preset[] = [
  { label: "25 / 5", focus: 25, break: 5, long: 15 },
  { label: "50 / 10", focus: 50, break: 10, long: 20 },
  { label: "15 / 3", focus: 15, break: 3, long: 10 },
];
const LONG_BREAK_EVERY = 4;
const STORAGE_KEY = "zivo-pomodoro-v1";
const POS_KEY = "zivo-pomodoro-pos-v1";
const PANEL_W = 232;

function phaseSeconds(preset: Preset, phase: Phase): number {
  return (phase === "focus" ? preset.focus : phase === "break" ? preset.break : preset.long) * 60;
}

function fmt(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** Two soft sine blips via Web Audio — a phase-change cue with no asset/copyright. */
function chime(up: boolean) {
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    const now = ctx.currentTime;
    const notes = up ? [523.25, 659.25] : [659.25, 440.0];
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const t = now + i * 0.18;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.18, t + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.34);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.36);
    });
    window.setTimeout(() => void ctx.close(), 1200);
  } catch {
    /* audio may be blocked; the visual phase change is enough */
  }
}

export function PomodoroWidget() {
  const [open, setOpen] = useState(false);
  const [presetIdx, setPresetIdx] = useState(0);
  const [phase, setPhase] = useState<Phase>("focus");
  const [remaining, setRemaining] = useState(() => PRESETS[0].focus * 60);
  const [running, setRunning] = useState(false);
  const [completed, setCompleted] = useState(0); // focus rounds finished
  const preset = PRESETS[presetIdx];

  // --- Draggable, position-persisted placement -------------------------------
  // Fixed to the viewport (independent of the sidebar), so expanding the sidebar
  // never nudges it. The user drags it anywhere and it remembers where it was.
  const [pos, setPos] = useState<{ x: number; y: number } | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      const raw = localStorage.getItem(POS_KEY);
      if (raw) {
        const p = JSON.parse(raw);
        if (typeof p?.x === "number" && typeof p?.y === "number") return p;
      }
    } catch {
      /* ignore */
    }
    // Default: bottom-right, so it never overlaps the left sidebar.
    return { x: window.innerWidth - PANEL_W - 16, y: window.innerHeight - 320 };
  });
  const posRef = useRef(pos);
  posRef.current = pos;
  const movedRef = useRef(false);

  const clampPos = useCallback(
    (p: { x: number; y: number }) => {
      if (typeof window === "undefined") return p;
      const w = open ? PANEL_W : 40;
      const h = open ? 300 : 40;
      return {
        x: Math.max(8, Math.min(p.x, window.innerWidth - w - 8)),
        y: Math.max(8, Math.min(p.y, window.innerHeight - h - 8)),
      };
    },
    [open],
  );

  // Keep it on-screen when the window resizes or the panel opens/closes.
  useEffect(() => {
    const reclamp = () => setPos((p) => (p ? clampPos(p) : p));
    reclamp();
    window.addEventListener("resize", reclamp);
    return () => window.removeEventListener("resize", reclamp);
  }, [clampPos]);

  // Drag from the launcher or the panel header. A <4px move counts as a click
  // (so tapping the launcher still opens it); anything more is a drag.
  const beginDrag = useCallback(
    (e: ReactPointerEvent) => {
      e.preventDefault();
      const start = { x: e.clientX, y: e.clientY };
      const origin = posRef.current ?? { x: 16, y: 16 };
      movedRef.current = false;
      const onMove = (ev: PointerEvent) => {
        const dx = ev.clientX - start.x;
        const dy = ev.clientY - start.y;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) movedRef.current = true;
        setPos(clampPos({ x: origin.x + dx, y: origin.y + dy }));
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        const p = posRef.current;
        if (p) {
          try {
            localStorage.setItem(POS_KEY, JSON.stringify(p));
          } catch {
            /* ignore */
          }
        }
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [clampPos],
  );

  // Restore persisted config (not the live countdown — a stale timer shouldn't
  // resume mid-count after a reload).
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const s = JSON.parse(raw);
      if (typeof s.presetIdx === "number" && PRESETS[s.presetIdx]) setPresetIdx(s.presetIdx);
      if (typeof s.completed === "number") setCompleted(s.completed);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time restore of persisted config
      if (typeof s.presetIdx === "number" && PRESETS[s.presetIdx]) setRemaining(PRESETS[s.presetIdx].focus * 60);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ presetIdx, completed }));
    } catch {
      /* ignore */
    }
  }, [presetIdx, completed]);

  const nextPhase = useCallback(
    (justFinished: Phase): { phase: Phase; completed: number } => {
      if (justFinished === "focus") {
        const done = completed + 1;
        const goLong = done % LONG_BREAK_EVERY === 0;
        return { phase: goLong ? "long" : "break", completed: done };
      }
      return { phase: "focus", completed };
    },
    [completed],
  );

  // The countdown. Uses an absolute deadline so a throttled/backgrounded tab
  // still lands on the right time when it catches up.
  const deadlineRef = useRef<number | null>(null);
  useEffect(() => {
    if (!running) {
      deadlineRef.current = null;
      return;
    }
    // Deadline is unavailable here (Date.now is fine in the browser at runtime).
    deadlineRef.current = Date.now() + remaining * 1000;
    const id = window.setInterval(() => {
      const left = Math.max(0, Math.round(((deadlineRef.current ?? 0) - Date.now()) / 1000));
      if (left > 0) {
        setRemaining(left);
        return;
      }
      // Phase complete → advance, chime, keep running into the next phase.
      setRemaining(0);
      const adv = nextPhase(phase);
      chime(adv.phase === "focus");
      setCompleted(adv.completed);
      setPhase(adv.phase);
      const nextSecs = phaseSeconds(preset, adv.phase);
      setRemaining(nextSecs);
      deadlineRef.current = Date.now() + nextSecs * 1000;
    }, 250);
    return () => window.clearInterval(id);
    // remaining intentionally omitted — the deadline captures it; re-subscribing
    // every tick would reset the interval.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, phase, preset, nextPhase]);

  const setPhaseFresh = useCallback(
    (p: Phase) => {
      setPhase(p);
      setRemaining(phaseSeconds(preset, p));
      deadlineRef.current = null;
    },
    [preset],
  );

  const reset = useCallback(() => {
    setRunning(false);
    setPhaseFresh("focus");
  }, [setPhaseFresh]);

  const skip = useCallback(() => {
    const adv = nextPhase(phase);
    setCompleted(adv.completed);
    setPhaseFresh(adv.phase);
  }, [nextPhase, phase, setPhaseFresh]);

  const changePreset = useCallback((idx: number) => {
    setPresetIdx(idx);
    setRunning(false);
    setPhase("focus");
    setRemaining(PRESETS[idx].focus * 60);
    deadlineRef.current = null;
  }, []);

  const total = phaseSeconds(preset, phase);
  const progress = total > 0 ? 1 - remaining / total : 0;
  const label = phase === "focus" ? "Focus" : phase === "break" ? "Break" : "Long break";
  const accent = phase === "focus" ? "lavender" : "sage";

  // Ring geometry for the circular progress.
  const R = 30;
  const C = 2 * Math.PI * R;
  const ringStyle = useMemo(
    () => ({ strokeDasharray: C, strokeDashoffset: C * (1 - progress) }),
    [C, progress],
  );

  if (!pos) return null;

  if (!open) {
    return (
      <Tooltip label="Focus timer · drag to move" position="right" withArrow>
        <ActionIcon
          onPointerDown={beginDrag}
          onClick={() => {
            if (!movedRef.current) setOpen(true);
          }}
          variant="default"
          size={40}
          radius="xl"
          aria-label="Open focus timer"
          style={{
            position: "fixed",
            left: pos.x,
            top: pos.y,
            zIndex: 130,
            boxShadow: "0 6px 20px rgba(35,34,32,0.14)",
            touchAction: "none",
            cursor: "grab",
          }}
        >
          <IconClockHour4 size={20} stroke={1.7} />
        </ActionIcon>
      </Tooltip>
    );
  }

  return (
    <Box
      style={{
        position: "fixed",
        left: pos.x,
        top: pos.y,
        zIndex: 130,
        width: PANEL_W,
        padding: 14,
        borderRadius: 18,
        background: "var(--mantine-color-body)",
        border: "1px solid var(--mantine-color-default-border)",
        boxShadow: "0 18px 50px rgba(35,34,32,0.20), 0 2px 8px rgba(35,34,32,0.08)",
      }}
    >
      <Group
        justify="space-between"
        mb={8}
        wrap="nowrap"
        onPointerDown={beginDrag}
        style={{ cursor: "grab", touchAction: "none" }}
      >
        <Group gap={7} wrap="nowrap">
          <IconClockHour4 size={16} stroke={1.8} style={{ color: `var(--mantine-color-${accent}-6)` }} />
          <Text size="sm" fw={600}>
            {label}
          </Text>
        </Group>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          radius="md"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => setOpen(false)}
          aria-label="Hide focus timer"
        >
          <IconChevronRight size={16} stroke={2} />
        </ActionIcon>
      </Group>

      <Box style={{ position: "relative", width: 132, height: 132, margin: "2px auto 8px" }}>
        <svg width={132} height={132} viewBox="0 0 132 132" style={{ transform: "rotate(-90deg)" }}>
          <circle cx={66} cy={66} r={R} fill="none" stroke="var(--mantine-color-default-border)" strokeWidth={7} />
          <circle
            cx={66}
            cy={66}
            r={R}
            fill="none"
            stroke={`var(--mantine-color-${accent}-5)`}
            strokeWidth={7}
            strokeLinecap="round"
            style={{ ...ringStyle, transition: "stroke-dashoffset 300ms linear" }}
          />
        </svg>
        <Box style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
          <Text style={{ fontSize: 28, fontWeight: 600, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.02em" }}>
            {fmt(remaining)}
          </Text>
          <Text size="xs" c="dimmed" mt={-2}>
            {completed} done
          </Text>
        </Box>
      </Box>

      <Group justify="center" gap={8} mb={10}>
        <Tooltip label="Reset" withArrow openDelay={400}>
          <ActionIcon variant="subtle" color="gray" size={34} radius="xl" onClick={reset} aria-label="Reset">
            <IconRotateClockwise size={16} stroke={1.8} />
          </ActionIcon>
        </Tooltip>
        <ActionIcon
          variant="filled"
          color={accent}
          size={44}
          radius="xl"
          onClick={() => setRunning((r) => !r)}
          aria-label={running ? "Pause" : "Start"}
        >
          {running ? <IconPlayerPause size={20} stroke={2} /> : <IconPlayerPlay size={20} stroke={2} />}
        </ActionIcon>
        <Tooltip label="Skip to next" withArrow openDelay={400}>
          <ActionIcon variant="subtle" color="gray" size={34} radius="xl" onClick={skip} aria-label="Skip">
            <IconPlayerSkipForward size={16} stroke={1.8} />
          </ActionIcon>
        </Tooltip>
      </Group>

      <Group gap={4} justify="center" wrap="nowrap">
        {PRESETS.map((p, i) => (
          <UnstyledButton
            key={p.label}
            onClick={() => changePreset(i)}
            style={{
              padding: "3px 9px",
              borderRadius: 999,
              fontSize: 11,
              fontWeight: 600,
              color: i === presetIdx ? `var(--mantine-color-${accent}-7)` : "var(--mantine-color-dimmed)",
              background: i === presetIdx ? `var(--mantine-color-${accent}-1)` : "transparent",
              border: `1px solid ${i === presetIdx ? `var(--mantine-color-${accent}-2)` : "transparent"}`,
            }}
          >
            {p.label}
          </UnstyledButton>
        ))}
      </Group>
    </Box>
  );
}
