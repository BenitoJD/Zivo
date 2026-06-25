"use client";

import {
  ActionIcon,
  Box,
  Button,
  Center,
  Group,
  NavLink,
  Progress,
  ScrollArea,
  SegmentedControl,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
  useMantineColorScheme,
} from "@mantine/core";
import {
  IconCpu,
  IconFileText,
  IconLayoutSidebarLeftCollapse,
  IconLogin,
  IconMoon,
  IconSun,
  IconTrash,
  IconUpload,
} from "@tabler/icons-react";
import { BRAND_NAME } from "@/lib/brand";
import { BrandMark } from "@/app/_components/BrandMark";
import type { SourceDocument } from "@/lib/types";

export const SIDEBAR_MINI_WIDTH = 64;
export const SIDEBAR_EXPANDED_WIDTH = 280;
export const SHELL_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";
export const SHELL_MS = 280;
const MINI_RAIL_ICON_SIZE = 42;

function SidebarAnimatedLayer({
  visible,
  children,
  enterDelay = 0,
  reduceMotion,
}: {
  visible: boolean;
  children: React.ReactNode;
  enterDelay?: number;
  reduceMotion: boolean;
}) {
  const duration = reduceMotion ? 0 : SHELL_MS;
  const delay = reduceMotion ? 0 : enterDelay;
  const closeMs = reduceMotion ? 0 : Math.round(duration * 0.45);
  return (
    <Box
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
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
        border: emphasized ? "1px solid var(--mantine-color-default-border)" : "none",
        borderRadius: "var(--mantine-radius-md)",
        background: active
          ? "var(--mantine-color-lavender-1)"
          : emphasized
            ? "var(--mantine-color-default)"
            : "transparent",
        cursor: disabled ? "default" : "pointer",
        padding: 0,
        color: active ? "var(--mantine-color-lavender-7)" : "var(--mantine-color-dimmed)",
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

export function sourceLabel(filename: string) {
  return filename.replace(/\.[^.]+$/, "");
}

export function sourceDescription(status: SourceDocument["status"], progress: number) {
  if (status === "indexing") return `Indexing ${progress}%`;
  if (status === "pending") return "Choose pages";
  return status;
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
  onDeleteSource: (doc: SourceDocument) => void;
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
  onDeleteSource,
}: SidebarProps) {
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";

  return (
    <>
      {/* Header row (desktop only) */}
      {!isMobile && (
        <Box pos="relative" h={52} w="100%">
          <SidebarAnimatedLayer visible={!wide} reduceMotion={reduceMotion}>
            <Center h={52}>
              <Tooltip label="Expand sidebar" position="right" withArrow>
                <UnstyledButton onClick={onToggleSidebar} aria-label="Expand sidebar" p={4}>
                  <BrandMark showWord={false} height={32} />
                </UnstyledButton>
              </Tooltip>
            </Center>
          </SidebarAnimatedLayer>
          <SidebarAnimatedLayer visible={wide} enterDelay={60} reduceMotion={reduceMotion}>
            <Group px="md" h={52} justify="space-between" wrap="nowrap" gap="sm">
              <Group gap="sm" wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
                <BrandMark showWord={false} height={30} />
                <Text
                  fw={600}
                  size="lg"
                  lineClamp={1}
                  style={{
                    letterSpacing: "-0.03em",
                    fontFamily: "var(--font-serif), 'EB Garamond', Georgia, serif",
                    color: "var(--mantine-color-text)",
                  }}
                >
                  {BRAND_NAME}
                </Text>
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
        <SidebarAnimatedLayer visible={!wide} reduceMotion={reduceMotion}>
          <Box h="100%" w="100%" style={{ overflowY: "auto", overflowX: "hidden" }}>
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
          </Box>
        </SidebarAnimatedLayer>

        <SidebarAnimatedLayer visible={wide} enterDelay={80} reduceMotion={reduceMotion}>
          <ScrollArea
            h="100%"
            type={isMobile ? "never" : "auto"}
            scrollbars="y"
            offsetScrollbars={!isMobile}
            px="xs"
            pt={isMobile ? "md" : "xs"}
          >
            <Text size="xs" tt="uppercase" fw={700} c="gray.5" px="sm" mb={6} lts={1}>
              Sources
            </Text>
            {documents.length === 0 ? (
              <Text size="sm" c="gray.5" px="sm" lh={1.5}>
                No sources yet. Add a PDF or article to start.
              </Text>
            ) : (
              documents.map((d) => {
                const lbl = sourceLabel(d.filename);
                const description = sourceDescription(d.status, d.index_progress);
                return (
                  <NavLink
                    key={d.id}
                    label={lbl}
                    description={description}
                    leftSection={<IconFileText size={18} stroke={1.5} />}
                    rightSection={
                      <Tooltip label="Delete source" position="left" withArrow>
                        <ActionIcon
                          variant="subtle"
                          color="terracotta"
                          size="sm"
                          aria-label={`Delete ${lbl}`}
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            onDeleteSource(d);
                          }}
                        >
                          <IconTrash size={16} stroke={1.5} />
                        </ActionIcon>
                      </Tooltip>
                    }
                    active={artifactId === d.id}
                    onClick={() => onNavigateSource(d.id)}
                    styles={{ root: { borderRadius: "var(--mantine-radius-md)" } }}
                  />
                );
              })
            )}
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
            {username && (
              <Stack gap="xs" mb={isMobile ? "sm" : "md"}>
                <Group justify="space-between">
                  <Text size="xs" c="gray.5">
                    Storage
                  </Text>
                  <Text size="xs" c="gray.6">
                    {storagePct}%
                  </Text>
                </Group>
                <Progress value={storagePct} size="sm" color="lavender" />
              </Stack>
            )}
            {isAdmin && (
              <NavLink
                label="LLM models"
                description="Enable or disable chat models"
                leftSection={<IconCpu size={18} stroke={1.5} />}
                active={pathname === "/workspace/models"}
                onClick={onOpenModels}
                mb="sm"
                styles={{ root: { borderRadius: "var(--mantine-radius-md)" } }}
              />
            )}
            <Button
              fullWidth
              size={isMobile ? "md" : "sm"}
              leftSection={<IconUpload size={16} />}
              onClick={onAddSource}
              mb={isMobile ? "xs" : "sm"}
            >
              Add source
            </Button>
            <Group justify="space-between" wrap="nowrap" align="center" gap="sm">
              <Group gap="xs" wrap="nowrap" align="center">
                <Text size="xs" c="gray.5">
                  Appearance
                </Text>
                <SegmentedControl
                  size="xs"
                  value={isDark ? "dark" : "light"}
                  onChange={(value) => {
                    if ((value === "dark") !== isDark) toggleColorScheme();
                  }}
                  data={[
                    {
                      value: "light",
                      label: (
                        <Center style={{ display: "flex", lineHeight: 1 }}>
                          <IconSun size={14} stroke={2} />
                        </Center>
                      ),
                    },
                    {
                      value: "dark",
                      label: (
                        <Center style={{ display: "flex", lineHeight: 1 }}>
                          <IconMoon size={14} stroke={2} />
                        </Center>
                      ),
                    },
                  ]}
                  aria-label={isDark ? "Dark mode on" : "Light mode on"}
                />
              </Group>
              <Button
                variant="subtle"
                size="compact-sm"
                leftSection={<IconLogin size={14} />}
                onClick={onSignIn}
                px="xs"
              >
                {username ? `@${username}` : "Sign in"}
              </Button>
            </Group>
          </Box>
        ) : (
          <Box pos="relative" mih={140} w="100%">
            <Stack gap={6} align="center" w="100%" py="xs">
              <MiniRailButton
                label={isDark ? "Switch to light mode" : "Switch to dark mode"}
                onClick={() => toggleColorScheme()}
              >
                {isDark ? <IconMoon size={18} stroke={1.5} /> : <IconSun size={18} stroke={1.5} />}
              </MiniRailButton>
              <MiniRailButton label="Add source" emphasized onClick={onAddSource}>
                <IconUpload size={18} stroke={1.5} />
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
