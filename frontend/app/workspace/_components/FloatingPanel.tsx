"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { ActionIcon, Box, Group, Text, Tooltip } from "@mantine/core";
import { IconMaximize, IconMinus, IconWindowMaximize, IconX } from "@tabler/icons-react";

/**
 * A modern floating window for the Source / Tutor panels: a movable, resizable
 * card that pops over the study area (instead of the old static left/right push
 * rails). Drag the title bar to move; drag any edge or corner to resize
 * (horizontal, vertical, diagonal); minimize to the header, maximize to fill the
 * workspace, or close. Geometry is bounded to the workspace and persisted.
 */

const HEADER_H = 38;
const MIN_W = 300;
const MIN_H = 220;
const MARGIN = 12;

type Rect = { x: number; y: number; w: number; h: number };
type Dir = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

// Shared stacking counter so clicking a panel brings it above the others.
let zTop = 40;
const nextZ = () => (zTop += 1);

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

function loadRect(key: string): Rect | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const r = JSON.parse(raw);
    if (typeof r?.x === "number" && typeof r?.w === "number") return r as Rect;
  } catch {
    /* ignore */
  }
  return null;
}

export function FloatingPanel({
  open,
  title,
  icon,
  accent = "lavender",
  storageKey,
  containerRef,
  defaultSide = "left",
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  icon?: ReactNode;
  accent?: string;
  storageKey: string;
  containerRef: RefObject<HTMLDivElement | null>;
  defaultSide?: "left" | "right";
  onClose: () => void;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<Rect | null>(null);
  const [minimized, setMinimized] = useState(false);
  const [maximized, setMaximized] = useState(false);
  // While actively dragging/resizing the panel must track the pointer 1:1 (no
  // transition). At rest it gets an eased position transition so the sidebar
  // collapse/expand — which nudges the panel to stay put — glides instead of
  // stepping jerkily with each ResizeObserver tick.
  const [interacting, setInteracting] = useState(false);
  const [z, setZ] = useState(() => nextZ());
  const restore = useRef<Rect | null>(null);

  const bounds = useCallback(() => {
    const el = containerRef.current;
    return el ? { w: el.clientWidth, h: el.clientHeight } : { w: 900, h: 600 };
  }, [containerRef]);

  // Initialise geometry the first time the panel opens (stored, else a sensible
  // half-width column on its default side).
  useEffect(() => {
    if (!open || rect) return;
    const { w: cw, h: ch } = bounds();
    const stored = loadRect(storageKey);
    let next: Rect;
    if (stored) {
      next = {
        x: clamp(stored.x, 0, Math.max(0, cw - MIN_W)),
        y: clamp(stored.y, 0, Math.max(0, ch - MIN_H)),
        w: clamp(stored.w, MIN_W, cw),
        h: clamp(stored.h, MIN_H, ch),
      };
    } else {
      const w = clamp(Math.round(cw * 0.42), MIN_W, Math.max(MIN_W, cw - 2 * MARGIN));
      const h = Math.max(MIN_H, ch - 2 * MARGIN);
      next = { x: defaultSide === "left" ? MARGIN : Math.max(MARGIN, cw - w - MARGIN), y: MARGIN, w, h };
    }
    // One-time geometry init measured from the live container (an external
    // system), not derived render state — the effect is the right place.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRect(next);
  }, [open, rect, storageKey, defaultSide, bounds]);

  // Persist + keep the window inside the workspace when it (or the window) resizes.
  useEffect(() => {
    if (!rect) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(rect));
    } catch {
      /* ignore */
    }
  }, [rect, storageKey]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    // Track the container's viewport-left so a sidebar collapse/expand (which
    // slides this container's left edge) can be compensated: without this the
    // absolutely-positioned panel lurches sideways with the reflow. We shift the
    // panel's x by the negative of that movement so it stays pinned in the viewport.
    let prevLeft = el.getBoundingClientRect().left;
    const ro = new ResizeObserver(() => {
      const leftNow = el.getBoundingClientRect().left;
      const shift = leftNow - prevLeft;
      prevLeft = leftNow;
      setRect((r) => {
        if (!r) return r;
        const { w: cw, h: ch } = bounds();
        return {
          w: clamp(r.w, MIN_W, cw),
          h: clamp(r.h, MIN_H, ch),
          x: clamp(r.x - shift, 0, Math.max(0, cw - Math.min(r.w, cw))),
          y: clamp(r.y, 0, Math.max(0, ch - HEADER_H)),
        };
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [containerRef, bounds]);

  const startDrag = useCallback(
    (mode: "move" | Dir, e: React.PointerEvent) => {
      if (!rect || maximized) return;
      e.preventDefault();
      const sx = e.clientX;
      const sy = e.clientY;
      const s = rect;
      const { w: cw, h: ch } = bounds();
      setInteracting(true);
      document.body.style.userSelect = "none";
      if (mode !== "move") document.body.style.cursor = `${mode}-resize`;

      const move = (ev: PointerEvent) => {
        const dx = ev.clientX - sx;
        const dy = ev.clientY - sy;
        if (mode === "move") {
          setRect({
            ...s,
            x: clamp(s.x + dx, 0, Math.max(0, cw - s.w)),
            y: clamp(s.y + dy, 0, Math.max(0, ch - HEADER_H)),
          });
          return;
        }
        let { x, y, w, h } = s;
        if (mode.includes("e")) w = clamp(s.w + dx, MIN_W, cw - s.x);
        if (mode.includes("s")) h = clamp(s.h + dy, MIN_H, ch - s.y);
        if (mode.includes("w")) {
          w = clamp(s.w - dx, MIN_W, s.x + s.w);
          x = s.x + s.w - w;
        }
        if (mode.includes("n")) {
          h = clamp(s.h - dy, MIN_H, s.y + s.h);
          y = s.y + s.h - h;
        }
        setRect({ x, y, w, h });
      };
      const up = () => {
        setInteracting(false);
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    },
    [rect, maximized, bounds],
  );

  const bringToFront = useCallback(() => setZ(nextZ()), []);

  const toggleMax = useCallback(() => {
    setMinimized(false);
    setMaximized((m) => {
      if (!m) restore.current = rect;
      else if (restore.current) setRect(restore.current);
      return !m;
    });
  }, [rect]);

  const toggleMin = useCallback(() => {
    setMaximized(false);
    setMinimized((v) => !v);
  }, []);

  if (!open || !rect) return null;

  // Maximized fills the workspace via inset (no need to read the container size
  // during render); otherwise use the stored rect (collapsed to the header when
  // minimized).
  const geom: React.CSSProperties = maximized
    ? { left: MARGIN, top: MARGIN, right: MARGIN, bottom: MARGIN }
    : { left: rect.x, top: rect.y, width: rect.w, height: minimized ? HEADER_H : rect.h };

  const handle = (dir: Dir, style: React.CSSProperties) => (
    <Box
      onPointerDown={(e) => startDrag(dir, e)}
      style={{ position: "absolute", zIndex: 2, touchAction: "none", ...style }}
    />
  );
  const showHandles = !maximized && !minimized;

  return (
    <Box
      ref={panelRef}
      onPointerDownCapture={bringToFront}
      style={{
        position: "absolute",
        ...geom,
        zIndex: z,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        background: "var(--mantine-color-body)",
        border: "1px solid var(--mantine-color-default-border)",
        borderRadius: 14,
        boxShadow: "0 18px 50px rgba(35, 34, 32, 0.22), 0 2px 8px rgba(35, 34, 32, 0.08)",
        // Eased glide at rest (smooths the sidebar-toggle reposition); none while
        // the user is dragging/resizing so the pointer stays perfectly tracked.
        transition: interacting
          ? "none"
          : "left 260ms cubic-bezier(0.32,0.72,0,1), top 260ms cubic-bezier(0.32,0.72,0,1)",
        willChange: "left, top",
      }}
    >
      {/* Title bar — drag to move; double-click to maximize/restore. */}
      <Group
        h={HEADER_H}
        px={10}
        gap={8}
        wrap="nowrap"
        justify="space-between"
        onPointerDown={(e) => startDrag("move", e)}
        onDoubleClick={toggleMax}
        style={{
          flexShrink: 0,
          cursor: maximized ? "default" : "move",
          borderBottom: minimized ? "none" : "1px solid var(--mantine-color-default-border)",
          background: `light-dark(var(--mantine-color-${accent}-0), var(--mantine-color-dark-6))`,
          touchAction: "none",
          userSelect: "none",
        }}
      >
        <Group gap={7} wrap="nowrap" style={{ minWidth: 0 }}>
          {icon ? (
            <Box style={{ display: "flex", color: `var(--mantine-color-${accent}-7)`, flexShrink: 0 }}>{icon}</Box>
          ) : null}
          <Text size="sm" fw={600} truncate c="var(--mantine-color-text)" style={{ letterSpacing: "-0.01em" }}>
            {title}
          </Text>
        </Group>
        <Group gap={2} wrap="nowrap" style={{ flexShrink: 0 }}>
          <Tooltip label={minimized ? "Restore" : "Minimize"} withArrow openDelay={400}>
            <ActionIcon variant="subtle" color="gray" size="sm" radius="md" onClick={toggleMin} aria-label="Minimize">
              <IconMinus size={15} stroke={2} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label={maximized ? "Restore" : "Maximize"} withArrow openDelay={400}>
            <ActionIcon variant="subtle" color="gray" size="sm" radius="md" onClick={toggleMax} aria-label="Maximize">
              {maximized ? <IconWindowMaximize size={14} stroke={2} /> : <IconMaximize size={14} stroke={2} />}
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Close" withArrow openDelay={400}>
            <ActionIcon variant="subtle" color="gray" size="sm" radius="md" onClick={onClose} aria-label={`Close ${title}`}>
              <IconX size={15} stroke={2} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      {!minimized && (
        <Box flex={1} mih={0} style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {children}
        </Box>
      )}

      {showHandles && (
        <>
          {handle("n", { top: -3, left: 8, right: 8, height: 8, cursor: "n-resize" })}
          {handle("s", { bottom: -3, left: 8, right: 8, height: 8, cursor: "s-resize" })}
          {handle("w", { left: -3, top: 8, bottom: 8, width: 8, cursor: "w-resize" })}
          {handle("e", { right: -3, top: 8, bottom: 8, width: 8, cursor: "e-resize" })}
          {handle("nw", { top: -4, left: -4, width: 14, height: 14, cursor: "nw-resize" })}
          {handle("ne", { top: -4, right: -4, width: 14, height: 14, cursor: "ne-resize" })}
          {handle("sw", { bottom: -4, left: -4, width: 14, height: 14, cursor: "sw-resize" })}
          {handle("se", { bottom: -4, right: -4, width: 14, height: 14, cursor: "se-resize" })}
        </>
      )}
    </Box>
  );
}
