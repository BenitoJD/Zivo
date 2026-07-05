"use client";

import { useRef, type ReactNode } from "react";
import { ActionIcon, Box, Group, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { IconGripVertical, IconX } from "@tabler/icons-react";
import { PANEL_EASE, PANEL_MS, clampPanel } from "@/app/workspace/_components/studyLayout";

/**
 * Study side rails (extracted from the workspace page monolith): the edge tab
 * that re-opens a collapsed Source/Tutor panel (StudyEdgeTrigger) and the
 * resizable push rail that hosts a side panel with a drag handle (StudyPushRail +
 * PanelResizeHandle).
 */
export function StudyEdgeTrigger({
  side,
  icon,
  label,
  color,
  onClick,
}: {
  side: "left" | "right";
  icon: ReactNode;
  label: string;
  color: string;
  onClick: () => void;
}) {
  return (
    <Tooltip label={`Open ${label}`} position={side === "left" ? "right" : "left"} withArrow openDelay={350}>
      <UnstyledButton
        onClick={onClick}
        aria-label={`Open ${label}`}
        className={`zivo-edge zivo-edge-${side}`}
        style={{ position: "absolute", top: "50%", [side]: 10, zIndex: 6 }}
      >
        <style>{`
          .zivo-edge {
            transform: translateY(-50%);
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 7px;
            padding: 10px 9px;
            border-radius: 16px;
            background: var(--mantine-color-gray-0);
            border: 1px solid var(--mantine-color-default-border);
            box-shadow: 0 8px 24px rgba(35, 34, 32, 0.10), 0 1px 2px rgba(35, 34, 32, 0.04);
            transition: transform 220ms cubic-bezier(0.32,0.72,0,1), box-shadow 220ms ease, border-color 220ms ease;
          }
          .zivo-edge:hover { box-shadow: 0 12px 32px rgba(35, 34, 32, 0.16), 0 2px 4px rgba(35, 34, 32, 0.06); }
          .zivo-edge-left:hover { transform: translateY(-50%) translateX(4px); }
          .zivo-edge-right:hover { transform: translateY(-50%) translateX(-4px); }
          .zivo-edge-chip {
            width: 32px; height: 32px; border-radius: 10px;
            display: flex; align-items: center; justify-content: center;
          }
          .zivo-edge-label { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; line-height: 1; color: var(--mantine-color-dimmed); }
          @media (prefers-reduced-motion: reduce) { .zivo-edge { transition: none; } }
        `}</style>
        <span
          className="zivo-edge-chip"
          style={{ background: `var(--mantine-color-${color}-0)`, color: `var(--mantine-color-${color}-7)` }}
        >
          {icon}
        </span>
        <span className="zivo-edge-label">{label}</span>
      </UnstyledButton>
    </Tooltip>
  );
}

export function StudyPushRail({
  open,
  width,
  minWidth,
  maxWidth,
  side,
  title,
  onClose,
  children,
  resizable = false,
  overlay = false,
  isResizing = false,
  onResizeStart,
  onResizeEnd,
  onWidthChange,
}: {
  open: boolean;
  width: number;
  minWidth?: number;
  maxWidth?: number;
  side: "left" | "right";
  title: string;
  onClose: () => void;
  children: ReactNode;
  headerSize?: "default" | "compact";
  resizable?: boolean;
  overlay?: boolean;
  isResizing?: boolean;
  onResizeStart?: () => void;
  onResizeEnd?: () => void;
  onWidthChange?: (width: number) => void;
}) {
  const railBorder = "1px solid var(--mantine-color-default-border)";
  // Tablet slide-over: cap the panel at ~58% of its row so the question column
  // underneath is never cramped, regardless of the stored drag width.
  const overlayWidth = Math.min(width, 460);

  return (
    <Box
      pos={overlay ? "absolute" : "relative"}
      h="100%"
      style={
        overlay
          ? {
              top: 0,
              bottom: 0,
              [side]: 0,
              width: open ? overlayWidth : 0,
              maxWidth: "82%",
              flexShrink: 0,
              overflow: "hidden",
              zIndex: open ? 20 : 1,
              transition: isResizing
                ? undefined
                : `width ${PANEL_MS}ms ${PANEL_EASE}, transform ${PANEL_MS}ms ${PANEL_EASE}`,
              transform: open
                ? "translateX(0)"
                : side === "left"
                  ? "translateX(-100%)"
                  : "translateX(100%)",
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
              boxShadow: open ? "0 12px 40px rgba(0, 0, 0, 0.35)" : undefined,
              borderRight: open && side === "left" ? railBorder : undefined,
              borderLeft: open && side === "right" ? railBorder : undefined,
              borderRadius: side === "left" ? "0 16px 16px 0" : "16px 0 0 16px",
            }
          : {
              width: open ? width : 0,
              flexShrink: 0,
              alignSelf: "stretch",
              overflow: "hidden",
              transition: isResizing ? undefined : `width ${PANEL_MS}ms ${PANEL_EASE}`,
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
              borderRight: open && side === "left" ? railBorder : undefined,
              borderLeft: open && side === "right" ? railBorder : undefined,
            }
      }
      >
      <Box
        w={overlay ? overlayWidth : width}
        h="100%"
        mih={0}
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          opacity: open ? 1 : 0,
          transform: open ? "translateX(0)" : side === "left" ? "translateX(-12px)" : "translateX(12px)",
          transition: isResizing
            ? undefined
            : `opacity ${PANEL_MS}ms ${PANEL_EASE} ${open ? 50 : 0}ms, transform ${PANEL_MS}ms ${PANEL_EASE} ${open ? 50 : 0}ms`,
          pointerEvents: open ? "auto" : "none",
        }}
      >
        <Group
          px="md"
          py={8}
          justify="space-between"
          wrap="nowrap"
          gap="xs"
          style={{
            flexShrink: 0,
            borderBottom: open ? railBorder : undefined,
            minHeight: 44,
            background: "var(--mantine-color-body)",
          }}
        >
          <Text size="sm" fw={600} truncate c="var(--mantine-color-text)" style={{ letterSpacing: "-0.01em" }}>
            {title}
          </Text>
          <ActionIcon variant="subtle" color="gray" radius="xl" size="md" onClick={onClose} aria-label={`Close ${title}`} style={{ flexShrink: 0 }}>
            <IconX size={17} stroke={1.8} />
          </ActionIcon>
        </Group>
        <Box flex={1} mih={0} style={{ display: "flex", flexDirection: "column" }}>
          {children}
        </Box>
      </Box>
      {open && resizable && !overlay && onWidthChange && (
        <PanelResizeHandle
          side={side}
          minWidth={minWidth ?? 280}
          maxWidth={maxWidth ?? 720}
          width={width}
          onResizeStart={onResizeStart}
          onResizeEnd={onResizeEnd}
          onWidthChange={onWidthChange}
        />
      )}
    </Box>
  );
}

function PanelResizeHandle({
  side,
  width,
  minWidth,
  maxWidth,
  onWidthChange,
  onResizeStart,
  onResizeEnd,
}: {
  side: "left" | "right";
  width: number;
  minWidth: number;
  maxWidth: number;
  onWidthChange: (width: number) => void;
  onResizeStart?: () => void;
  onResizeEnd?: () => void;
}) {
  const dragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  function endDrag(target: EventTarget & Element, pointerId: number) {
    dragging.current = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    if (target.hasPointerCapture(pointerId)) {
      target.releasePointerCapture(pointerId);
    }
    onResizeEnd?.();
  }

  return (
    <Tooltip label="Drag to resize" position={side === "left" ? "right" : "left"} withArrow openDelay={500}>
      <Box
        role="separator"
        aria-orientation="vertical"
        aria-valuenow={width}
        aria-valuemin={minWidth}
        aria-valuemax={maxWidth}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          dragging.current = true;
          startX.current = e.clientX;
          startWidth.current = width;
          onResizeStart?.();
          document.body.style.cursor = "col-resize";
          document.body.style.userSelect = "none";
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!dragging.current) return;
          const delta = e.clientX - startX.current;
          const next =
            side === "left" ? startWidth.current + delta : startWidth.current - delta;
          onWidthChange(clampPanel(next, minWidth, maxWidth));
        }}
        onPointerUp={(e) => endDrag(e.currentTarget, e.pointerId)}
        onPointerCancel={(e) => endDrag(e.currentTarget, e.pointerId)}
        style={{
          position: "absolute",
          top: 0,
          bottom: 0,
          [side === "left" ? "right" : "left"]: 0,
          transform: side === "left" ? "translateX(50%)" : "translateX(-50%)",
          width: 10,
          cursor: "col-resize",
          zIndex: 30,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          touchAction: "none",
        }}
      >
        <Box
          w={4}
          h={48}
          style={{
            borderRadius: 999,
            background: "var(--mantine-color-default-border)",
            opacity: 0.9,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <IconGripVertical size={12} stroke={1.5} color="var(--mantine-color-dimmed)" />
        </Box>
      </Box>
    </Tooltip>
  );
}
