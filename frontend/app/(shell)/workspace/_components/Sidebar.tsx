"use client";

import { useEffect, useRef } from "react";
import {
  ActionIcon,
  Box,
  Button,
  Center,
  Collapse,
  Group,
  NavLink,
  Progress,
  ScrollArea,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
  useMantineColorScheme,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconArticle,
  IconBook2,
  IconBriefcase,
  IconBuildingCastle,
  IconBulb,
  IconChevronDown,
  IconCode,
  IconFileCv,
  IconCards,
  IconClipboardList,
  IconCpu,
  IconFileText,
  IconLayoutSidebarLeftCollapse,
  IconNews,
  IconListCheck,
  IconLogin,
  IconMessage2,
  IconMoon,
  IconNotebook,
  IconPlus,
  IconChartBar,
  IconRss,
  IconSettings,
  IconSun,
  IconTool,
  IconTrash,
  IconWriting,
  IconBrain,
} from "@tabler/icons-react";
import { BrandMark } from "@/app/_components/BrandMark";
import type { SourceDocument } from "@/lib/types";
import { useStudyNav, type StudyMode } from "@/app/workspace/_components/studyNav";

/**
 * Mode navigator below sources when an artifact is open.
 * Study = gold chrome (Read / Learn / Test). Tools = quieter secondary modes.
 * Icons stay monochrome (ink / lavender active) - no rainbow chrome.
 */
const MODE_GROUPS: {
  heading: string;
  items: { value: StudyMode; label: string; icon: typeof IconBook2 }[];
}[] = [
  {
    heading: "Study",
    items: [
      { value: "read", label: "Read", icon: IconBook2 },
      { value: "learn", label: "Learn", icon: IconBulb },
      { value: "test", label: "Test", icon: IconClipboardList },
    ],
  },
  {
    heading: "Tools",
    items: [
      { value: "progress", label: "Progress", icon: IconChartBar },
      { value: "brainstorm", label: "Brainstorm", icon: IconBrain },
      { value: "explain", label: "Explain", icon: IconMessage2 },
      { value: "notes", label: "Notes", icon: IconNotebook },
      { value: "cards", label: "Cards", icon: IconCards },
      { value: "palace", label: "Palace", icon: IconBuildingCastle },
      { value: "quiz", label: "Quiz", icon: IconListCheck },
      { value: "interview", label: "Interview", icon: IconBriefcase },
      { value: "coding", label: "Coding", icon: IconCode },
      { value: "resume", label: "Resume", icon: IconFileCv },
      { value: "mains", label: "Mains", icon: IconWriting },
    ],
  },
];

export const SIDEBAR_MINI_WIDTH = 64;
export const SIDEBAR_EXPANDED_WIDTH = 280;
export const SHELL_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";
// A touch longer than a snap - the rail glides open and pushes the page with it,
// settling on the brand ease for a buttery, Apple-like expand.
export const SHELL_MS = 340;
const MINI_RAIL_ICON_SIZE = 42;

function SidebarAnimatedLayer({
  visible,
  children,
  enterDelay = 0,
  reduceMotion,
  width,
}: {
  visible: boolean;
  children: React.ReactNode;
  enterDelay?: number;
  reduceMotion: boolean;
  /** Pin the layer to its final width (desktop) so its content stays laid out
   *  while only the navbar clip animates - no per-frame reflow, so the expand
   *  reads as a smooth reveal instead of a stuttering re-layout. */
  width?: number;
}) {
  const duration = reduceMotion ? 0 : SHELL_MS;
  const delay = reduceMotion ? 0 : enterDelay;
  const closeMs = reduceMotion ? 0 : Math.round(duration * 0.45);
  return (
    <Box
      style={{
        position: "absolute",
        top: 0,
        bottom: 0,
        left: 0,
        width: width ? `${width}px` : "100%",
        overflow: "hidden",
        opacity: visible ? 1 : 0,
        transition: visible
          ? `opacity ${duration}ms ${SHELL_EASE} ${delay}ms`
          : `opacity ${closeMs}ms ease-in`,
        pointerEvents: visible ? "auto" : "none",
        zIndex: visible ? 2 : 1,
      }}
      aria-hidden={!visible}
    >
      {children}
    </Box>
  );
}

function MiniRailButton({
  label,
  onClick,
  active = false,
  emphasized = false,
  disabled = false,
  children,
}: {
  label: string;
  onClick?: () => void;
  active?: boolean;
  emphasized?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  const button = (
    <Box
      component="button"
      type="button"
      disabled={disabled}
      onClick={onClick}
      w={MINI_RAIL_ICON_SIZE}
      h={MINI_RAIL_ICON_SIZE}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        border: emphasized ? "1px solid var(--mantine-color-lavender-2)" : "none",
        borderRadius: "var(--mantine-radius-md)",
        background: active
          ? "var(--mantine-color-lavender-1)"
          : emphasized
            ? "var(--mantine-color-lavender-1)"
            : "transparent",
        cursor: disabled ? "default" : "pointer",
        padding: 0,
        color:
          active || emphasized ? "var(--mantine-color-lavender-7)" : "var(--mantine-color-dimmed)",
        opacity: disabled ? 0.45 : 1,
      }}
      aria-label={label}
    >
      {children}
    </Box>
  );

  if (disabled) return button;
  return (
    <Tooltip label={label} position="right" withArrow>
      {button}
    </Tooltip>
  );
}

/** Mini (collapsed) mode navigator - a column of mode icons under the source icons. */
function MiniModeNav({ mode, onChange }: { mode: StudyMode; onChange: (m: StudyMode) => void }) {
  return (
    <Stack gap={8} align="center" w="100%" pt={8} mt={4} style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
      {MODE_GROUPS.flatMap((g) => g.items).map((it) => (
        <MiniRailButton key={it.value} label={it.label} active={mode === it.value} onClick={() => onChange(it.value)}>
          <it.icon size={18} stroke={1.7} />
        </MiniRailButton>
      ))}
    </Stack>
  );
}

/** Expanded mode navigator - grouped, labelled rows, placed below the source list. */
function ExpandedModeNav({ mode, onChange }: { mode: StudyMode; onChange: (m: StudyMode) => void }) {
  return (
    <Box mt="md" pt="md" style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
      <style>{`
        .zv-mode-row {
          display: flex; align-items: center; gap: 10px; width: 100%;
          padding: 9px 12px; border-radius: 12px; color: var(--mantine-color-dimmed);
          transition: background 150ms ease, color 150ms ease;
        }
        .zv-mode-row:hover { background: var(--mantine-color-default-hover); color: var(--mantine-color-text); }
        .zv-mode-row[data-active="true"] { background: var(--mantine-color-lavender-0); color: var(--mantine-color-lavender-7); font-weight: 600; }
        [data-mantine-color-scheme="dark"] .zv-mode-row[data-active="true"] { background: var(--mantine-color-lavender-1); color: var(--mantine-color-lavender-9); }
        @media (prefers-reduced-motion: reduce) { .zv-mode-row { transition: none !important; } }
      `}</style>
      <Stack gap="md">
        {MODE_GROUPS.map((g) => (
          <Stack key={g.heading} gap={4}>
            <Text size="xs" tt="uppercase" fw={700} c="dimmed" lts={1.4} px="sm" mb={2} style={{ fontSize: 11 }}>
              {g.heading}
            </Text>
            {g.items.map((it) => {
              const active = mode === it.value;
              return (
                <UnstyledButton
                  key={it.value}
                  className="zv-mode-row"
                  data-active={active}
                  onClick={() => onChange(it.value)}
                  aria-current={active ? "page" : undefined}
                >
                  <it.icon size={18} stroke={active ? 2 : 1.6} style={{ flexShrink: 0 }} />
                  <Text size="sm" style={{ fontWeight: "inherit" }}>{it.label}</Text>
                </UnstyledButton>
              );
            })}
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}

export function sourceLabel(filename: string) {
  return filename.replace(/\.[^.]+$/, "");
}

/**
 * Status only while there is something to say - a ready source stays quiet.
 * (A list where every row shouts "Ready" is a list saying nothing.)
 */
function sourceStatusMeta(status: SourceDocument["status"], progress: number) {
  if (status === "indexing")
    return { dot: "var(--mantine-color-lavender-5)", label: `Indexing ${progress}%`, pulse: true };
  if (status === "pending")
    return { dot: "var(--mantine-color-gray-5)", label: "Choose pages", pulse: false };
  if (status === "ready" || status === "indexed") return null;
  return { dot: "var(--mantine-color-gray-5)", label: status, pulse: false };
}

/**
 * A single source in the expanded rail - Wispr-calm: a tight, single-line row with
 * a tinted glyph chip, a truncated title, and a quiet status dot. The active row
 * lifts onto a soft lavender surface; the delete affordance stays hidden until hover.
 */
function SourceRow({
  doc,
  active,
  onNavigate,
  onDelete,
}: {
  doc: SourceDocument;
  active: boolean;
  onNavigate: () => void;
  onDelete: () => void;
}) {
  const label = sourceLabel(doc.filename);
  const meta = sourceStatusMeta(doc.status, doc.index_progress);
  const titleRef = useRef<HTMLParagraphElement>(null);
  // On hover, slide a truncated name to reveal its hidden end ("run" it), back on leave.
  const revealTitle = (on: boolean) => {
    const el = titleRef.current;
    if (!el) return;
    const overflow = el.scrollWidth - el.clientWidth;
    el.style.textIndent = on && overflow > 1 ? `-${overflow}px` : "0px";
  };
  return (
    <UnstyledButton
      className="zivo-source-row"
      data-active={active || undefined}
      onClick={onNavigate}
      onMouseEnter={() => revealTitle(true)}
      onMouseLeave={() => revealTitle(false)}
      aria-label={label}
    >
      <span className="zivo-source-chip" aria-hidden>
        <IconFileText size={16} stroke={1.7} />
        {meta && (
          <span
            className={meta.pulse ? "zivo-chip-dot zivo-chip-dot-pulse" : "zivo-chip-dot"}
            style={{ background: meta.dot }}
          />
        )}
      </span>
      <span className="zivo-source-body">
        <Text
          ref={titleRef}
          size="sm"
          fw={500}
          truncate
          className="zivo-source-title"
          style={{ transition: "text-indent 1.6s ease" }}
        >
          {label}
        </Text>
        {meta && (
          <Text size="xs" c="dimmed" truncate className="zivo-source-statusline">
            {meta.label}
          </Text>
        )}
        {doc.status === "indexing" && (
          <span className="zivo-source-track" aria-hidden>
            <span
              className="zivo-source-fill"
              style={{ width: `${Math.max(4, Math.min(100, doc.index_progress))}%` }}
            />
          </span>
        )}
      </span>
      <Tooltip label="Delete source" position="right" withArrow openDelay={300}>
        <span
          role="button"
          tabIndex={-1}
          aria-label={`Delete ${label}`}
          className="zivo-source-del"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onDelete();
          }}
        >
          <IconTrash size={15} stroke={1.7} />
        </span>
      </Tooltip>
    </UnstyledButton>
  );
}

export type SidebarProps = {
  /** Source list. */
  documents: SourceDocument[];
  /** Currently-viewed artifact id (for active highlight). */
  artifactId?: string;
  /** Whether the expanded (wide) view is showing. */
  wide: boolean;
  /** Whether this is the mobile drawer (affects padding/scroll). */
  isMobile: boolean;
  reduceMotion: boolean;
  username: string | null;
  isAdmin: boolean;
  storagePct: number;
  pathname: string;
  onToggleSidebar: () => void;
  onNavigateSource: (id: string) => void;
  onAddSource: () => void;
  onSignIn: () => void;
  onOpenModels: () => void;
  onOpenNewspaper?: () => void;
  onOpenNewspaperPractice?: () => void;
  onOpenLearnAdmin?: () => void;
  onOpenProgress: () => void;
  onOpenCodingBank?: () => void;
  onDeleteSource: (doc: SourceDocument) => void;
  onOpenSettings: () => void;
};

export function Sidebar({
  documents,
  artifactId,
  wide,
  isMobile,
  reduceMotion,
  username,
  isAdmin,
  storagePct,
  pathname,
  onToggleSidebar,
  onNavigateSource,
  onAddSource,
  onSignIn,
  onOpenModels,
  onOpenNewspaper,
  onOpenNewspaperPractice,
  onOpenLearnAdmin,
  onOpenProgress,
  onOpenCodingBank,
  onDeleteSource,
  onOpenSettings,
}: SidebarProps) {
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  // When an artifact study view is mounted, host its mode navigator here (below
  // the sources) rather than as a separate rail.
  const { active: studyActive, mode: studyMode, setMode: setStudyMode } = useStudyNav();

  const onAdminRoute =
    pathname === "/workspace/models" ||
    pathname === "/workspace/newspaper" ||
    pathname === "/workspace/learn";
  const [adminOpen, { toggle: toggleAdmin, open: openAdmin }] = useDisclosure(onAdminRoute);
  useEffect(() => {
    if (onAdminRoute) openAdmin();
  }, [onAdminRoute, openAdmin]);

  const navLinkStyles = {
    root: { borderRadius: "var(--mantine-radius-md)", paddingTop: 8, paddingBottom: 8 },
  } as const;

  return (
    <>
      <style>{`
        /* Source row - Wispr-calm: one quiet line per ready source, tinted glyph chip,
           hover-reveal delete. Status appears only while something is happening. */
        .zivo-source-row {
          position: relative;
          width: 100%;
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 7px 10px;
          min-height: 46px;
          border-radius: 12px;
          border: 1px solid transparent;
          transition: background 140ms ease, border-color 140ms ease;
        }
        .zivo-source-row:hover { background: var(--mantine-color-default-hover); }
        .zivo-source-row:focus-visible {
          outline: 2px solid var(--mantine-color-lavender-4);
          outline-offset: -1px;
        }
        .zivo-source-row[data-active] {
          background: var(--mantine-color-lavender-0);
          border-color: var(--mantine-color-lavender-2);
        }
        /* lavender-0/2 are remapped in dark, but bump the active wash one step
           so the selected source still lifts off the ink page body. */
        [data-mantine-color-scheme="dark"] .zivo-source-row[data-active] {
          background: var(--mantine-color-lavender-1);
          border-color: var(--mantine-color-lavender-3);
        }
        .zivo-source-chip {
          position: relative;
          flex-shrink: 0;
          width: 32px;
          height: 32px;
          border-radius: 10px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--mantine-color-default-hover);
          color: var(--mantine-color-dimmed);
          transition: background 140ms ease, color 140ms ease;
        }
        .zivo-source-row:hover .zivo-source-chip { background: var(--mantine-color-gray-2); }
        .zivo-source-row[data-active] .zivo-source-chip {
          background: var(--mantine-color-lavender-1);
          color: var(--mantine-color-lavender-7);
        }
        /* Busy signal lives on the chip's corner, ringed by the page so it reads as a badge. */
        .zivo-chip-dot {
          position: absolute;
          right: -2px;
          top: -2px;
          width: 9px;
          height: 9px;
          border-radius: 999px;
          box-shadow: 0 0 0 2px var(--mantine-color-body);
        }
        .zivo-chip-dot-pulse { animation: zivo-dot-pulse 1.6s ease-in-out infinite; }
        @keyframes zivo-dot-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
        .zivo-source-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; text-align: left; }
        .zivo-source-row[data-active] .zivo-source-title { color: var(--mantine-color-lavender-8); }
        [data-mantine-color-scheme="dark"] .zivo-source-row[data-active] .zivo-source-title {
          color: var(--mantine-color-lavender-9);
        }
        .zivo-source-statusline { line-height: 1.3; }
        .zivo-source-track {
          height: 3px;
          margin-top: 3px;
          border-radius: 999px;
          background: var(--mantine-color-default-hover);
          overflow: hidden;
        }
        .zivo-source-fill {
          display: block;
          height: 100%;
          border-radius: 999px;
          background: var(--mantine-color-lavender-5);
          transition: width 400ms ease;
        }
        .zivo-source-del {
          position: absolute;
          right: 8px;
          top: 50%;
          transform: translateY(-50%);
          width: 28px;
          height: 28px;
          border-radius: 8px;
          display: flex;
          align-items: center;
          justify-content: center;
          color: var(--mantine-color-dimmed);
          background: var(--mantine-color-gray-2);
          opacity: 0;
          transition: opacity 120ms ease, background 140ms ease, color 140ms ease;
          cursor: pointer;
        }
        .zivo-source-row:hover .zivo-source-del,
        .zivo-source-row:focus-visible .zivo-source-del { opacity: 1; }
        @media (hover: none) { .zivo-source-del { opacity: 1; } }
        .zivo-source-del:hover { background: var(--mantine-color-terracotta-0); color: var(--mantine-color-terracotta-6); }

        /* Section header count - a soft pill beside the label, not a stray number. */
        .zivo-count-pill {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 18px;
          height: 17px;
          padding: 0 5px;
          border-radius: 999px;
          background: var(--mantine-color-default-hover);
          color: var(--mantine-color-dimmed);
          font-size: 10.5px;
          font-weight: 600;
          font-variant-numeric: tabular-nums;
        }

        /* Add source - the sidebar's one hero: a soft lavender gradient pill with an
           inner top highlight, lifting gently on hover. Calm, tactile, unmistakable. */
        .zivo-add-source {
          background: linear-gradient(180deg, #F3E4FC 0%, #E9D2F9 100%) !important;
          color: #3A2455 !important;
          border: 1px solid #DFC5F2 !important;
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.55), 0 1px 2px rgba(55, 34, 89, 0.06) !important;
          font-weight: 600 !important;
          transition: background 160ms ease, transform 140ms ease, box-shadow 160ms ease !important;
        }
        .zivo-add-source:hover {
          background: linear-gradient(180deg, #EFDBFB 0%, #E3C7F7 100%) !important;
          transform: translateY(-1px);
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.55), 0 3px 10px rgba(55, 34, 89, 0.12) !important;
        }
        .zivo-add-source:active { transform: translateY(0); box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4), 0 1px 2px rgba(55, 34, 89, 0.06) !important; }
        [data-mantine-color-scheme="dark"] .zivo-add-source {
          background: linear-gradient(180deg, var(--mantine-color-lavender-3) 0%, var(--mantine-color-lavender-2) 100%) !important;
          border-color: var(--mantine-color-lavender-3) !important;
          color: var(--mantine-color-lavender-9) !important;
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.07), 0 1px 2px rgba(0, 0, 0, 0.25) !important;
        }
        [data-mantine-color-scheme="dark"] .zivo-add-source:hover {
          background: linear-gradient(180deg, #4C4168 0%, var(--mantine-color-lavender-3) 100%) !important;
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.07), 0 4px 14px rgba(0, 0, 0, 0.35) !important;
        }

        /* Footer utility bar - account chip on the left, quiet icon actions on the right. */
        .zivo-account {
          display: flex;
          align-items: center;
          gap: 8px;
          min-width: 0;
          flex: 1;
          padding: 4px 10px 4px 4px;
          border-radius: 999px;
          transition: background 140ms ease;
        }
        .zivo-account:hover { background: var(--mantine-color-default-hover); }
        .zivo-account:focus-visible { outline: 2px solid var(--mantine-color-lavender-4); }
        .zivo-avatar {
          flex-shrink: 0;
          width: 26px;
          height: 26px;
          border-radius: 999px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--mantine-color-lavender-1);
          color: var(--mantine-color-lavender-7);
          font-size: 12px;
          font-weight: 700;
          text-transform: uppercase;
        }
        @media (prefers-reduced-motion: reduce) {
          .zivo-add-source, .zivo-source-row, .zivo-source-chip, .zivo-source-del,
          .zivo-source-fill, .zivo-account { transition: none !important; }
          .zivo-chip-dot-pulse { animation: none !important; }
        }
      `}</style>
      {/* Header row (desktop only) */}
      {!isMobile && (
        <Box pos="relative" h={52} w="100%">
          <SidebarAnimatedLayer visible={!wide} reduceMotion={reduceMotion} width={isMobile ? undefined : SIDEBAR_MINI_WIDTH}>
            <Center h={52}>
              <Tooltip label="Expand sidebar" position="right" withArrow>
                <UnstyledButton onClick={onToggleSidebar} aria-label="Expand sidebar" p={4}>
                  <BrandMark showWord={false} height={32} />
                </UnstyledButton>
              </Tooltip>
            </Center>
          </SidebarAnimatedLayer>
          <SidebarAnimatedLayer visible={wide} enterDelay={60} reduceMotion={reduceMotion} width={isMobile ? undefined : SIDEBAR_EXPANDED_WIDTH}>
            <Group px="md" h={52} justify="space-between" wrap="nowrap" gap="sm">
              <Group gap="sm" wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
                <BrandMark showWord={true} height={30} />
              </Group>
              <ActionIcon
                variant="subtle"
                color="gray"
                onClick={onToggleSidebar}
                aria-label="Collapse sidebar"
                style={{ flexShrink: 0 }}
              >
                <IconLayoutSidebarLeftCollapse size={18} stroke={1.5} />
              </ActionIcon>
            </Group>
          </SidebarAnimatedLayer>
        </Box>
      )}

      {/* Source list */}
      <Box style={{ flex: 1, minHeight: 0, overflow: "hidden", position: "relative" }}>
        <SidebarAnimatedLayer visible={!wide} reduceMotion={reduceMotion} width={isMobile ? undefined : SIDEBAR_MINI_WIDTH}>
          <Box className="zv-noscrollbar" h="100%" w="100%" style={{ overflowY: "auto", overflowX: "hidden" }}>
            <Stack gap={6} align="center" w="100%" py={4}>
              {documents.length === 0 ? (
                <MiniRailButton label="No sources yet" disabled>
                  <IconFileText size={18} stroke={1.5} />
                </MiniRailButton>
              ) : (
                documents.map((d) => {
                  const lbl = sourceLabel(d.filename);
                  return (
                    <MiniRailButton
                      key={d.id}
                      label={lbl}
                      active={artifactId === d.id}
                      onClick={() => onNavigateSource(d.id)}
                    >
                      <IconFileText size={18} stroke={1.5} />
                    </MiniRailButton>
                  );
                })
              )}
            </Stack>
            {studyActive && <MiniModeNav mode={studyMode} onChange={setStudyMode} />}
          </Box>
        </SidebarAnimatedLayer>

        <SidebarAnimatedLayer visible={wide} enterDelay={80} reduceMotion={reduceMotion} width={isMobile ? undefined : SIDEBAR_EXPANDED_WIDTH}>
          <ScrollArea
            h="100%"
            type={isMobile ? "never" : "scroll"}
            scrollbars="y"
            scrollbarSize={8}
            scrollHideDelay={600}
            px="sm"
            pt={isMobile ? "md" : "xs"}
          >
            <Group gap={7} align="center" px="sm" mb={10} mt={2}>
              <Text size="xs" tt="uppercase" fw={700} c="dimmed" lts={1.4} style={{ fontSize: 11 }}>
                Sources
              </Text>
              {documents.length > 0 && <span className="zivo-count-pill">{documents.length}</span>}
            </Group>
            {documents.length === 0 ? (
              <Stack align="center" gap={6} px="md" py="lg" ta="center">
                <Box
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: 12,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: "var(--mantine-color-default-hover)",
                    color: "var(--mantine-color-dimmed)",
                  }}
                >
                  <IconFileText size={20} stroke={1.5} />
                </Box>
                <Text size="sm" c="dimmed" lh={1.5} maw={200}>
                  No sources yet. Add a PDF, doc, or notes to start studying.
                </Text>
              </Stack>
            ) : (
              <Stack gap={4}>
                {documents.map((d) => (
                  <SourceRow
                    key={d.id}
                    doc={d}
                    active={artifactId === d.id}
                    onNavigate={() => onNavigateSource(d.id)}
                    onDelete={() => onDeleteSource(d)}
                  />
                ))}
              </Stack>
            )}
            {studyActive && <ExpandedModeNav mode={studyMode} onChange={setStudyMode} />}
          </ScrollArea>
        </SidebarAnimatedLayer>
      </Box>

      {/* Footer controls */}
      <Box
        style={{
          flexShrink: 0,
          overflow: "hidden",
          borderTop: wide ? "1px solid var(--mantine-color-default-border)" : undefined,
        }}
      >
        {wide ? (
          <Box p={isMobile ? "sm" : "md"} w="100%" pb={isMobile ? "calc(var(--mantine-spacing-sm) + env(safe-area-inset-bottom))" : undefined}>
            {/* Storage only when it has something to say - a flat 0% bar is noise. */}
            {username && storagePct > 0 && (
              <Stack gap={6} mb="md" px={4}>
                <Group justify="space-between">
                  <Text c="dimmed" fw={500} style={{ fontSize: 11 }}>
                    Storage
                  </Text>
                  <Text c="dimmed" fw={600} style={{ fontSize: 11, fontVariantNumeric: "tabular-nums" }}>
                    {storagePct}%
                  </Text>
                </Group>
                <Progress value={storagePct} size={5} radius="xl" color="lavender" />
              </Stack>
            )}

            <Stack gap={4} mb="sm">
              <Text size="xs" tt="uppercase" fw={700} c="dimmed" lts={1.4} px="xs" mb={2} style={{ fontSize: 11 }}>
                Practice
              </Text>
              <NavLink
                label="Progress"
                leftSection={<IconChartBar size={18} stroke={1.6} />}
                active={pathname === "/workspace/progress"}
                onClick={onOpenProgress}
                styles={navLinkStyles}
              />
              {onOpenCodingBank && (
                <NavLink
                  label="Coding"
                  leftSection={<IconCode size={18} stroke={1.6} />}
                  active={
                    pathname.startsWith("/workspace/coding") || pathname.startsWith("/practice/coding")
                  }
                  onClick={onOpenCodingBank}
                  styles={navLinkStyles}
                />
              )}
              {onOpenNewspaperPractice && (
                <NavLink
                  label="Newspaper"
                  leftSection={<IconNews size={18} stroke={1.6} />}
                  active={pathname.startsWith("/practice/newspaper")}
                  onClick={onOpenNewspaperPractice}
                  styles={navLinkStyles}
                />
              )}
            </Stack>

            {isAdmin && (
              <Box mb="sm">
                <UnstyledButton
                  onClick={toggleAdmin}
                  aria-expanded={adminOpen}
                  w="100%"
                  px="xs"
                  py={8}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    borderRadius: "var(--mantine-radius-md)",
                    color: "var(--mantine-color-dimmed)",
                  }}
                >
                  <IconTool size={16} stroke={1.6} />
                  <Text size="xs" tt="uppercase" fw={700} lts={1.4} style={{ flex: 1, fontSize: 11, textAlign: "left" }}>
                    Admin
                  </Text>
                  <IconChevronDown
                    size={14}
                    stroke={1.8}
                    style={{
                      transform: adminOpen ? "rotate(180deg)" : "rotate(0deg)",
                      transition: reduceMotion ? undefined : "transform 180ms cubic-bezier(0.32, 0.72, 0, 1)",
                    }}
                  />
                </UnstyledButton>
                <Collapse expanded={adminOpen}>
                  <Stack gap={2} mt={2}>
                    <NavLink
                      label="Models"
                      leftSection={<IconCpu size={18} stroke={1.6} />}
                      active={pathname === "/workspace/models"}
                      onClick={onOpenModels}
                      styles={navLinkStyles}
                    />
                    {onOpenNewspaper && (
                      <NavLink
                        label="Paper source"
                        leftSection={<IconRss size={18} stroke={1.6} />}
                        active={pathname === "/workspace/newspaper"}
                        onClick={onOpenNewspaper}
                        styles={navLinkStyles}
                      />
                    )}
                    {onOpenLearnAdmin && (
                      <NavLink
                        label="Learn posts"
                        leftSection={<IconArticle size={18} stroke={1.6} />}
                        active={pathname === "/workspace/learn"}
                        onClick={onOpenLearnAdmin}
                        styles={navLinkStyles}
                      />
                    )}
                  </Stack>
                </Collapse>
              </Box>
            )}

            <Button
              fullWidth
              size={isMobile ? "md" : "sm"}
              radius="xl"
              leftSection={<IconPlus size={16} stroke={2.2} />}
              onClick={onAddSource}
              mb={isMobile ? "sm" : "md"}
              className="zivo-add-source"
            >
              Add source
            </Button>
            <Group gap={2} align="center" wrap="nowrap">
              <UnstyledButton
                className="zivo-account"
                onClick={onSignIn}
                aria-label={username ? `Account @${username}` : "Sign in"}
              >
                <span className="zivo-avatar" aria-hidden>
                  {username ? username[0] : <IconLogin size={14} stroke={1.8} />}
                </span>
                <Text size="sm" fw={500} truncate>
                  {username ? username : "Sign in"}
                </Text>
              </UnstyledButton>
              <Tooltip label={isDark ? "Light mode" : "Dark mode"} withArrow>
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  size={34}
                  radius="md"
                  onClick={() => toggleColorScheme()}
                  aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
                >
                  {isDark ? <IconSun size={17} stroke={1.7} /> : <IconMoon size={17} stroke={1.7} />}
                </ActionIcon>
              </Tooltip>
              <Tooltip label="Settings" withArrow>
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  size={34}
                  radius="md"
                  onClick={onOpenSettings}
                  aria-label="Settings"
                >
                  <IconSettings size={17} stroke={1.7} />
                </ActionIcon>
              </Tooltip>
            </Group>
          </Box>
        ) : (
          <Box pos="relative" mih={180} w="100%">
            <Stack gap={6} align="center" w="100%" py="xs">
              <MiniRailButton
                label={isDark ? "Switch to light mode" : "Switch to dark mode"}
                onClick={() => toggleColorScheme()}
              >
                {isDark ? <IconMoon size={18} stroke={1.5} /> : <IconSun size={18} stroke={1.5} />}
              </MiniRailButton>
              <MiniRailButton label="Progress" active={pathname === "/workspace/progress"} onClick={onOpenProgress}>
                <IconChartBar size={18} stroke={1.5} />
              </MiniRailButton>
              <MiniRailButton label="Settings" onClick={onOpenSettings}>
                <IconSettings size={18} stroke={1.5} />
              </MiniRailButton>
              <MiniRailButton label="Add source" emphasized onClick={onAddSource}>
                <IconPlus size={18} stroke={2} />
              </MiniRailButton>
              <MiniRailButton label={username ? `@${username}` : "Sign in"} onClick={onSignIn}>
                <IconLogin size={18} stroke={1.5} />
              </MiniRailButton>
            </Stack>
          </Box>
        )}
      </Box>
    </>
  );
}
