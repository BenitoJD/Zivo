"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { ActionIcon, Box, Group, Text, Tooltip } from "@mantine/core";
import {
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarRightCollapse,
  IconMaximize,
  IconMinus,
  IconWindowMaximize,
  IconX,
} from "@tabler/icons-react";

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
const ABS_MIN_W = 220;
const MARGIN = 12;

export type FloatingPanelRect = { x: number; y: number; w: number; h: number };
type Dir = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

export type FloatingPanelGeometry = {
  rect: FloatingPanelRect;
  minimized: boolean;
  maximized: boolean;
  open: boolean;
};

export type FloatingPanelHandle = {
  snapSide: (side: "left" | "right") => void;
};

// Shared stacking counter so clicking a panel brings it above the others.
let zTop = 40;
const nextZ = () => (zTop += 1);

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

/** Vertical bounds for user-driven move/resize (full viewport; not the meta-bar default). */
function moveYBounds(ch: number, panelH: number, minimized: boolean) {
  const h = minimized ? HEADER_H : panelH;
  return { min: MARGIN, max: Math.max(MARGIN, ch - h - MARGIN) };
}

/** Default y floor so new/docked panels clear StudyMetaBar without blocking drag-up. */
function defaultTopMin(topInset: number) {
  return Math.max(MARGIN, topInset);
}

/** Never ask for more width than the workspace can give (narrow tablets). */
function minPanelW(cw: number) {
  return Math.min(MIN_W, Math.max(ABS_MIN_W, cw - 2 * MARGIN));
}

function loadRect(key: string): FloatingPanelRect | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const r = JSON.parse(raw);
    if (typeof r?.x === "number" && typeof r?.w === "number") return r as FloatingPanelRect;
  } catch {
    /* ignore */
  }
  return null;
}

export const FloatingPanel = forwardRef<FloatingPanelHandle, {
  open: boolean;
  title: string;
  icon?: ReactNode;
  accent?: string;
  storageKey: string;
  containerRef: RefObject<HTMLDivElement | null>;
  defaultSide?: "left" | "right";
  onClose: () => void;
  onGeometryChange?: (geometry: FloatingPanelGeometry | null) => void;
  /** Keep the panel clear of StudyMetaBar (alignment controls). Defaults to 0. */
  topInset?: number;
  children: ReactNode;
}>(function FloatingPanel(
  {
    open,
    title,
    icon,
    accent = "lavender",
    storageKey,
    containerRef,
    defaultSide = "left",
    onClose,
    onGeometryChange,
    topInset = 0,
    children,
  },
  ref,
) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<FloatingPanelRect | null>(null);
  const [minimized, setMinimized] = useState(false);
  const [maximized, setMaximized] = useState(false);
  const [interacting, setInteracting] = useState(false);
  const [z, setZ] = useState(() => nextZ());
  const restore = useRef<FloatingPanelRect | null>(null);

  const bounds = useCallback(() => {
    const el = containerRef.current;
    return el ? { w: el.clientWidth, h: el.clientHeight } : { w: 900, h: 600 };
  }, [containerRef]);

  const snapSide = useCallback(
    (side: "left" | "right") => {
      setMinimized(false);
      setMaximized(false);
      setRect((current) => {
        const { w: cw, h: ch } = bounds();
        const floorW = minPanelW(cw);
        const topMin = defaultTopMin(topInset);
        const w = clamp(current?.w ?? Math.round(cw * 0.42), floorW, cw);
        const h = clamp(current?.h ?? Math.max(MIN_H, ch - topMin - MARGIN), MIN_H, ch);
        const x = side === "left" ? MARGIN : Math.max(MARGIN, cw - w - MARGIN);
        // Keep the panel below the meta bar even on a side dock: never reuse the
        // old y if it was parked in the reserved strip.
        const y = current && current.y >= topMin ? current.y : topMin;
        return { x, y, w, h };
      });
    },
    [bounds, topInset],
  );

  useImperativeHandle(ref, () => ({ snapSide }), [snapSide]);

  useEffect(() => {
    if (!open || !rect) {
      onGeometryChange?.(null);
      return;
    }
    onGeometryChange?.({ rect, minimized, maximized, open });
  }, [open, rect, minimized, maximized, onGeometryChange]);

  useEffect(() => {
    if (!open || rect) return;
    const { w: cw, h: ch } = bounds();
    const floorW = minPanelW(cw);
    // The panel must clear the StudyMetaBar strip at the top of the study row, so
    // clamp its y to the reserved inset instead of 0.
    const topMin = defaultTopMin(topInset);
    const stored = loadRect(storageKey);
    let next: FloatingPanelRect;
    if (stored) {
      const { min: yMin, max: yMax } = moveYBounds(ch, stored.h, false);
      next = {
        x: clamp(stored.x, 0, Math.max(0, cw - floorW)),
        y: clamp(stored.y, yMin, Math.max(yMin, yMax)),
        w: clamp(stored.w, floorW, cw),
        h: clamp(stored.h, MIN_H, ch),
      };
    } else {
      const w = clamp(Math.round(cw * 0.42), floorW, Math.max(floorW, cw - 2 * MARGIN));
      const h = Math.max(MIN_H, ch - topMin - MARGIN);
      next = { x: defaultSide === "left" ? MARGIN : Math.max(MARGIN, cw - w - MARGIN), y: topMin, w, h };
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time init from container
    setRect(next);
  }, [open, rect, storageKey, defaultSide, bounds, topInset]);

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
    let prevLeft = el.getBoundingClientRect().left;
    const ro = new ResizeObserver(() => {
      const leftNow = el.getBoundingClientRect().left;
      const shift = leftNow - prevLeft;
      prevLeft = leftNow;
      setRect((r) => {
        if (!r) return r;
        const { w: cw, h: ch } = bounds();
        const floorW = minPanelW(cw);
        const { min: yMin, max: yMax } = moveYBounds(ch, r.h, minimized);
        return {
          w: clamp(r.w, floorW, cw),
          h: clamp(r.h, MIN_H, ch),
          x: clamp(r.x - shift, 0, Math.max(0, cw - Math.min(r.w, cw))),
          y: clamp(r.y, yMin, Math.max(yMin, yMax)),
        };
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [containerRef, bounds, minimized]);

  const startDrag = useCallback(
    (mode: "move" | Dir, e: React.PointerEvent) => {
      if (!rect || maximized) return;
      e.preventDefault();
      const sx = e.clientX;
      const sy = e.clientY;
      const s = rect;
      const { w: cw, h: ch } = bounds();
      const floorW = minPanelW(cw);
      const { min: yMin, max: yMax } = moveYBounds(ch, s.h, minimized);
      setInteracting(true);
      document.body.style.userSelect = "none";
      if (mode !== "move") document.body.style.cursor = `${mode}-resize`;

      const move = (ev: PointerEvent) => {
        const dx = ev.clientX - sx;
        const dy = ev.clientY - sy;
        if (mode === "move") {
          setRect({
            ...s,
            x: clamp(s.x + dx, MARGIN, Math.max(MARGIN, cw - s.w - MARGIN)),
            y: clamp(s.y + dy, yMin, Math.max(yMin, yMax)),
          });
          return;
        }
        let { x, y, w, h } = s;
        if (mode.includes("e")) w = clamp(s.w + dx, floorW, cw - s.x);
        if (mode.includes("s")) h = clamp(s.h + dy, MIN_H, ch - s.y - MARGIN);
        if (mode.includes("w")) {
          w = clamp(s.w - dx, floorW, s.x + s.w);
          x = s.x + s.w - w;
        }
        if (mode.includes("n")) {
          h = clamp(s.h - dy, MIN_H, s.y + s.h - MARGIN);
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
    [rect, maximized, minimized, bounds],
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

  const geom: React.CSSProperties = maximized
    ? { left: MARGIN, top: defaultTopMin(topInset), right: MARGIN, bottom: MARGIN }
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
        transition: interacting
          ? "none"
          : "left 260ms cubic-bezier(0.32,0.72,0,1), top 260ms cubic-bezier(0.32,0.72,0,1)",
        willChange: "left, top",
      }}
    >
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
          background: `light-dark(var(--mantine-color-${accent}-0), var(--mantine-color-${accent}-1))`,
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
          {!maximized ? (
            <>
              <Tooltip label="Dock left" withArrow openDelay={400}>
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  size="sm"
                  radius="md"
                  onClick={() => snapSide("left")}
                  aria-label="Dock panel left"
                >
                  <IconLayoutSidebarLeftCollapse size={15} stroke={2} />
                </ActionIcon>
              </Tooltip>
              <Tooltip label="Dock right" withArrow openDelay={400}>
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  size="sm"
                  radius="md"
                  onClick={() => snapSide("right")}
                  aria-label="Dock panel right"
                >
                  <IconLayoutSidebarRightCollapse size={15} stroke={2} />
                </ActionIcon>
              </Tooltip>
            </>
          ) : null}
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
});
