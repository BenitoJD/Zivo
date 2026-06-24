"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type CSSProperties } from "react";
import { usePathname, useRouter } from "next/navigation";
import Image from "next/image";
import {
  ActionIcon,
  AppShell,
  Box,
  Burger,
  Button,
  Center,
  Group,
  Modal,
  NavLink,
  PasswordInput,
  Progress,
  ScrollArea,
  SegmentedControl,
  Stack,
  Tabs,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
  UnstyledButton,
  useMantineColorScheme,
} from "@mantine/core";
import { Dropzone, MIME_TYPES } from "@mantine/dropzone";
import { useForm } from "@mantine/form";
import { useDisclosure, useLocalStorage, useMediaQuery, useMounted } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconCpu, IconFileText, IconLayoutSidebarLeftCollapse, IconLink, IconLogin, IconMoon, IconSun, IconTrash, IconUpload, IconX } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiPost, apiUploadFile, ensureGuestSession, isArtifactId, setCsrfToken } from "@/lib/api/client";
import { queryKeys, useInvalidateSources, useSessionQuery, useSourcesQuery } from "@/lib/api/queries";
import { BRAND_LOGO_SRC, BRAND_NAME, BRAND_LOGO_WIDTH, BRAND_LOGO_HEIGHT } from "@/lib/brand";
import { STORAGE_LIMIT_BYTES } from "@/lib/constants";
import type { SourceDocument } from "@/lib/types";

/** Frosted backdrop for modals. */
const MODAL_OVERLAY_PROPS = {
  backgroundOpacity: 0.6,
  blur: 8,
} as const;

const SIDEBAR_MINI_WIDTH = 56;
const SIDEBAR_EXPANDED_WIDTH = 280;
const MOBILE_HEADER_HEIGHT = 48;
const MINI_RAIL_ICON_SIZE = 40;
const SHELL_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";
const SHELL_MS = 280;

type AddSourceTab = "file" | "url" | "paste" | "github";

type WorkspaceShellContextValue = {
  openAddSource: () => void;
};

const WorkspaceShellContext = createContext<WorkspaceShellContextValue | null>(null);

export function useWorkspaceShell() {
  const ctx = useContext(WorkspaceShellContext);
  if (!ctx) {
    throw new Error("useWorkspaceShell must be used within WorkspaceLayout");
  }
  return ctx;
}

function BrandLogo({ height }: { height: number }) {
  const width = Math.round((height * BRAND_LOGO_WIDTH) / BRAND_LOGO_HEIGHT);
  return (
    <Image
      src={BRAND_LOGO_SRC}
      alt={BRAND_NAME}
      width={width}
      height={height}
      priority
      unoptimized
      style={{
        width,
        height,
        flexShrink: 0,
        objectFit: "contain",
        display: "block",
      }}
    />
  );
}

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
          ? "var(--mantine-color-lavender-light)"
          : emphasized
            ? "var(--mantine-color-body)"
            : "transparent",
        cursor: disabled ? "default" : "pointer",
        padding: 0,
        color: active ? "var(--mantine-color-lavender-filled)" : "var(--mantine-color-dimmed)",
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

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const artifactId = pathname.startsWith("/workspace/") ? pathname.split("/")[2] : undefined;
  const [mobileOpened, { toggle: toggleMobile, close: closeMobile }] = useDisclosure(false);
  const [sidebarExpanded, setSidebarExpanded] = useLocalStorage({
    key: "zivo-sidebar-expanded",
    defaultValue: false,
  });
  const isMobile = useMediaQuery("(max-width: 48em)", false, {
    getInitialValueInEffect: true,
  });
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)", false, {
    getInitialValueInEffect: true,
  });
  const sidebarWide = isMobile ? mobileOpened : sidebarExpanded;
  const sidebarWidth = isMobile
    ? "100%"
    : sidebarWide
      ? SIDEBAR_EXPANDED_WIDTH
      : SIDEBAR_MINI_WIDTH;
  const mounted = useMounted();
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";

  const [addOpen, setAddOpen] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<SourceDocument | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const invalidateSources = useInvalidateSources();
  const queryClient = useQueryClient();
  const sourcesQuery = useSourcesQuery();
  const sessionQuery = useSessionQuery();
  const documents = sourcesQuery.data ?? [];
  const username = sessionQuery.data?.username ?? null;
  const isAdmin = Boolean(sessionQuery.data?.is_admin);

  const [importUrl, setImportUrl] = useState("");
  const [importPaste, setImportPaste] = useState("");
  const [importGithub, setImportGithub] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [addSourceTab, setAddSourceTab] = useState<AddSourceTab>("file");
  const uploadInFlight = useRef(false);
  const uploadAbortRef = useRef<AbortController | null>(null);

  const [authMode, setAuthMode] = useState<"login" | "signup">("login");
  const [authError, setAuthError] = useState("");
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const authForm = useForm({
    initialValues: { username: "", password: "" },
    validate: {
      username: (v) => (v.trim().length < 2 ? "Username required" : null),
      password: (v) => (v.length < 4 ? "Password too short" : null),
    },
  });

  const loadDocs = useCallback(async () => {
    await invalidateSources();
  }, [invalidateSources]);

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  useEffect(() => {
    if (sourcesQuery.error) {
      notifications.show({
        title: "Could not load library",
        message: sourcesQuery.error instanceof Error ? sourcesQuery.error.message : "Unknown error",
        color: "red",
      });
    }
  }, [sourcesQuery.error]);

  useEffect(() => {
    return () => {
      uploadAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (isMobile) closeMobile();
  }, [pathname, isMobile, closeMobile]);

  const storagePct = Math.min(
    100,
    Math.round((documents.reduce((s, d) => s + d.size_bytes, 0) / STORAGE_LIMIT_BYTES) * 100),
  );

  function onUploaded(id: string | undefined) {
    if (!isArtifactId(id)) {
      notifications.show({
        title: "Could not open source",
        message: "Upload finished without a valid document id. Try again from the library.",
        color: "red",
      });
      return;
    }
    void loadDocs();
    router.push(`/workspace/${id}`);
  }

  async function uploadFile(file: File) {
    if (uploadInFlight.current) return;
    uploadInFlight.current = true;
    uploadAbortRef.current?.abort();
    const abort = new AbortController();
    uploadAbortRef.current = abort;
    setImportBusy(true);
    setUploadProgress(0);
    try {
      await ensureGuestSession();
      const data = await apiUploadFile<{ id: string }>(file, {
        signal: abort.signal,
        onProgress: (loaded, total) => {
          if (total > 0) setUploadProgress(Math.round((loaded / total) * 100));
        },
      });
      notifications.show({ title: "Uploaded", message: file.name, color: "green" });
      setImportUrl("");
      setImportPaste("");
      setImportGithub("");
      onUploaded(data.id);
      setAddOpen(false);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      const message =
        e instanceof Error && e.name === "TimeoutError"
          ? "Upload timed out. Try a smaller file or check your connection."
          : e instanceof Error
            ? e.message
            : "Unknown error";
      notifications.show({
        title: "Upload failed",
        message,
        color: "red",
      });
    } finally {
      if (uploadAbortRef.current === abort) uploadAbortRef.current = null;
      uploadInFlight.current = false;
      setImportBusy(false);
      setUploadProgress(null);
    }
  }

  async function runImport(kind: "url" | "paste" | "github") {
    setImportBusy(true);
    try {
      await ensureGuestSession();
      const path =
        kind === "github"
          ? "/api/sources/import-github"
          : kind === "paste"
            ? "/api/sources/import-text"
            : "/api/sources/import-url";
      const body =
        kind === "url"
          ? { url: importUrl }
          : kind === "paste"
            ? { text: importPaste, title: "Pasted source" }
            : { github_url: importGithub };
      const data = await apiPost<{ id: string }>(path, body);
      notifications.show({ title: "Imported", message: "Source added", color: "green" });
      setImportUrl("");
      setImportPaste("");
      setImportGithub("");
      onUploaded(data.id);
      setAddOpen(false);
    } catch (e) {
      notifications.show({
        title: "Import failed",
        message: e instanceof Error ? e.message : "Unknown error",
        color: "red",
      });
    } finally {
      setImportBusy(false);
    }
  }

  async function submitAuth(values: typeof authForm.values) {
    setAuthSubmitting(true);
    setAuthError("");
    try {
      const path = authMode === "login" ? "/api/auth/login" : "/api/auth/signup";
      const payload =
        authMode === "login"
          ? { username: values.username, password: values.password, remember_me: false }
          : {
              username: values.username,
              password: values.password,
              confirm_password: values.password,
              accept_terms: true,
            };
      const res = await apiPost<{ username: string; csrf_token: string; is_admin?: boolean }>(path, payload);
      setCsrfToken(res.csrf_token);
      queryClient.setQueryData(queryKeys.session, res);
      authForm.reset();
      setAuthOpen(false);
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Auth failed");
    } finally {
      setAuthSubmitting(false);
    }
  }

  function toggleSidebar() {
    if (isMobile) {
      toggleMobile();
      return;
    }
    setSidebarExpanded(!sidebarExpanded);
  }

  function sourceLabel(filename: string) {
    return filename.replace(/\.[^.]+$/, "");
  }

  function sourceDescription(status: SourceDocument["status"], progress: number) {
    if (status === "indexing") return `Indexing ${progress}%`;
    if (status === "pending") return "Choose pages";
    return status;
  }

  async function confirmDeleteSource() {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setDeleteBusy(true);
    try {
      await ensureGuestSession();
      await apiDelete(`/api/sources/${target.id}`);
      const remaining = documents.filter((d) => d.id !== target.id);
      setDeleteTarget(null);
      if (artifactId === target.id) {
        router.push(remaining[0] ? `/workspace/${remaining[0].id}` : "/workspace");
      }
      await loadDocs();
      notifications.show({
        title: "Source deleted",
        message: `${sourceLabel(target.filename)} was removed permanently.`,
        color: "green",
      });
    } catch (e) {
      notifications.show({
        title: "Could not delete source",
        message: e instanceof Error ? e.message : "Unknown error",
        color: "red",
      });
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <WorkspaceShellContext.Provider value={{ openAddSource: () => setAddOpen(true) }}>
      <AppShell
        transitionDuration={reduceMotion ? 0 : SHELL_MS}
        transitionTimingFunction={SHELL_EASE}
        header={{ height: { base: MOBILE_HEADER_HEIGHT, sm: 0 } }}
        navbar={{
          width: sidebarWidth,
          breakpoint: "sm",
          collapsed: { mobile: !mobileOpened, desktop: false },
        }}
        padding={{ base: "xs", sm: "md" }}
        styles={{
          root: {
            height: "100dvh",
            overflow: "hidden",
          },
          header: {
            borderBottom: "1px solid var(--mantine-color-default-border)",
          },
          navbar: {
            overflow: "hidden",
          },
          main: {
            height: "100%",
            minHeight: 0,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        <AppShell.Header hiddenFrom="sm" px="sm">
          <Group h="100%" justify="space-between" wrap="nowrap" align="center">
            <Box w={34} style={{ flexShrink: 0 }}>
              <Burger
                opened={mobileOpened}
                onClick={toggleMobile}
                size="sm"
                aria-label={mobileOpened ? "Close navigation" : "Open navigation"}
              />
            </Box>
            <Group gap="xs" wrap="nowrap" justify="center" style={{ flex: 1, minWidth: 0 }}>
              <BrandLogo height={28} />
              <Title order={5} lineClamp={1} style={{ letterSpacing: "-0.03em" }}>
                {BRAND_NAME}
              </Title>
            </Group>
            {mobileOpened ? (
              <Box w={34} aria-hidden style={{ flexShrink: 0 }} />
            ) : (
              <ActionIcon
                variant="subtle"
                color="gray"
                aria-label="Add source"
                onClick={() => setAddOpen(true)}
                w={34}
                style={{ flexShrink: 0 }}
              >
                <IconUpload size={18} stroke={1.5} />
              </ActionIcon>
            )}
          </Group>
        </AppShell.Header>
        <AppShell.Navbar p={0}>
          {!isMobile && (
            <AppShell.Section p={0} style={{ flexShrink: 0, overflow: "hidden" }}>
              <Box pos="relative" h={48} w="100%">
                <SidebarAnimatedLayer
                  visible={!sidebarWide}
                  reduceMotion={Boolean(reduceMotion)}
                >
                  <Center h={48}>
                    <Tooltip label="Expand sidebar" position="right" withArrow>
                      <UnstyledButton onClick={toggleSidebar} aria-label="Expand sidebar" p={4}>
                        <BrandLogo height={32} />
                      </UnstyledButton>
                    </Tooltip>
                  </Center>
                </SidebarAnimatedLayer>
                <SidebarAnimatedLayer visible={sidebarWide} enterDelay={60} reduceMotion={Boolean(reduceMotion)}>
                  <Group px="md" h={48} justify="space-between" wrap="nowrap" gap="sm">
                    <Group gap="sm" wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
                      <BrandLogo height={34} />
                      <Title order={4} lineClamp={1} style={{ letterSpacing: "-0.03em" }}>
                        {BRAND_NAME}
                      </Title>
                    </Group>
                    <ActionIcon
                      variant="subtle"
                      color="gray"
                      onClick={toggleSidebar}
                      aria-label="Collapse sidebar"
                      style={{ flexShrink: 0 }}
                    >
                      <IconLayoutSidebarLeftCollapse size={18} stroke={1.5} />
                    </ActionIcon>
                  </Group>
                </SidebarAnimatedLayer>
              </Box>
            </AppShell.Section>
          )}
          <AppShell.Section grow p={0} style={{ minHeight: 0, overflow: "hidden", position: "relative" }}>
            <SidebarAnimatedLayer
              visible={!sidebarWide}
              reduceMotion={Boolean(reduceMotion)}
            >
              <Box h="100%" w="100%" style={{ overflowY: "auto", overflowX: "hidden" }}>
                <Stack gap={6} align="center" w="100%" py={4}>
                  {documents.length === 0 ? (
                    <MiniRailButton label="No sources yet" disabled>
                      <IconFileText size={18} stroke={1.5} />
                    </MiniRailButton>
                  ) : (
                    documents.map((d) => {
                      const label = sourceLabel(d.filename);
                      return (
                        <MiniRailButton
                          key={d.id}
                          label={label}
                          active={artifactId === d.id}
                          onClick={() => {
                            router.push(`/workspace/${d.id}`);
                            if (isMobile) closeMobile();
                          }}
                        >
                          <IconFileText size={18} stroke={1.5} />
                        </MiniRailButton>
                      );
                    })
                  )}
                </Stack>
              </Box>
            </SidebarAnimatedLayer>
            <SidebarAnimatedLayer visible={sidebarWide} enterDelay={80} reduceMotion={Boolean(reduceMotion)}>
              <ScrollArea
                h="100%"
                type={isMobile ? "never" : "auto"}
                scrollbars="y"
                offsetScrollbars={isMobile ? false : true}
                px="xs"
                pt={isMobile ? "md" : "xs"}
              >
                <Text size="xs" tt="uppercase" fw={600} c="dimmed" px="sm" mb={4} lts={0.6}>
                  Sources
                </Text>
                {documents.length === 0 ? (
                  <Text size="sm" c="dimmed" px="sm">
                    No sources yet. Add a PDF or article to start.
                  </Text>
                ) : (
                  documents.map((d) => {
                    const label = sourceLabel(d.filename);
                    const description = sourceDescription(d.status, d.index_progress);
                    return (
                      <NavLink
                        key={d.id}
                        label={label}
                        description={description}
                        leftSection={<IconFileText size={18} stroke={1.5} />}
                        rightSection={
                          <Tooltip label="Delete source" position="left" withArrow>
                            <ActionIcon
                              variant="subtle"
                              color="red"
                              size="sm"
                              aria-label={`Delete ${label}`}
                              onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                setDeleteTarget(d);
                              }}
                            >
                              <IconTrash size={16} stroke={1.5} />
                            </ActionIcon>
                          </Tooltip>
                        }
                        active={artifactId === d.id}
                        onClick={() => {
                          router.push(`/workspace/${d.id}`);
                          if (isMobile) closeMobile();
                        }}
                        styles={{
                          root: {
                            borderRadius: "var(--mantine-radius-md)",
                          },
                        }}
                      />
                    );
                  })
                )}
              </ScrollArea>
            </SidebarAnimatedLayer>
          </AppShell.Section>
          <AppShell.Section p={0} style={{ flexShrink: 0, overflow: "hidden", borderTop: sidebarWide ? "1px solid var(--mantine-color-default-border)" : undefined }}>
            {sidebarWide ? (
              <Box p={isMobile ? "sm" : "md"} w="100%" pb={isMobile ? "calc(var(--mantine-spacing-sm) + env(safe-area-inset-bottom))" : undefined}>
                {username && (
                  <Stack gap="xs" mb={isMobile ? "sm" : "md"}>
                    <Group justify="space-between">
                      <Text size="xs" c="dimmed">
                        Storage
                      </Text>
                      <Text size="xs">{storagePct}%</Text>
                    </Group>
                    <Progress value={storagePct} size="sm" />
                  </Stack>
                )}
                {isAdmin && (
                  <NavLink
                    label="LLM models"
                    description="Enable or disable chat models"
                    leftSection={<IconCpu size={18} stroke={1.5} />}
                    active={pathname === "/workspace/models"}
                    onClick={() => {
                      router.push("/workspace/models");
                      if (isMobile) closeMobile();
                    }}
                    mb="sm"
                    styles={{
                      root: {
                        borderRadius: "var(--mantine-radius-md)",
                      },
                    }}
                  />
                )}
                <Button
                  fullWidth
                  variant="white"
                  c="dark.9"
                  size={isMobile ? "md" : "sm"}
                  leftSection={<IconUpload size={16} />}
                  onClick={() => {
                    setAddOpen(true);
                    if (isMobile) closeMobile();
                  }}
                  mb={isMobile ? "xs" : "sm"}
                >
                  Add source
                </Button>
                <Group justify="space-between" wrap="nowrap" align="center" gap="sm">
                  <Group gap="xs" wrap="nowrap" align="center">
                    <Text size="xs" c="dimmed">
                      Appearance
                    </Text>
                    {mounted ? (
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
                    ) : (
                      <SegmentedControl
                        size="xs"
                        value="dark"
                        readOnly
                        data={[
                          { value: "light", label: <IconSun size={14} stroke={2} /> },
                          { value: "dark", label: <IconMoon size={14} stroke={2} /> },
                        ]}
                      />
                    )}
                  </Group>
                  <Button
                    variant="subtle"
                    size="compact-sm"
                    leftSection={<IconLogin size={14} />}
                    onClick={() => {
                      setAuthOpen(true);
                      if (isMobile) closeMobile();
                    }}
                    px="xs"
                  >
                    {username ? `@${username}` : "Sign in"}
                  </Button>
                </Group>
              </Box>
            ) : (
              <Box pos="relative" mih={132} w="100%">
                <Stack gap={6} align="center" w="100%" py="xs">
                  <MiniRailButton
                    label={isDark ? "Switch to light mode" : "Switch to dark mode"}
                    onClick={() => toggleColorScheme()}
                  >
                    {isDark ? <IconMoon size={18} stroke={1.5} /> : <IconSun size={18} stroke={1.5} />}
                  </MiniRailButton>
                  <MiniRailButton label="Add source" emphasized onClick={() => setAddOpen(true)}>
                    <IconUpload size={18} stroke={1.5} />
                  </MiniRailButton>
                  <MiniRailButton label={username ? `@${username}` : "Sign in"} onClick={() => setAuthOpen(true)}>
                    <IconLogin size={18} stroke={1.5} />
                  </MiniRailButton>
                </Stack>
              </Box>
            )}
          </AppShell.Section>
        </AppShell.Navbar>
        <AppShell.Main>{children}</AppShell.Main>
      </AppShell>

      <Modal
        opened={deleteTarget !== null}
        onClose={() => {
          if (!deleteBusy) setDeleteTarget(null);
        }}
        title="Delete source?"
        centered
        overlayProps={MODAL_OVERLAY_PROPS}
        closeOnClickOutside={!deleteBusy}
        closeOnEscape={!deleteBusy}
      >
        <Stack gap="md">
          <Text size="sm">
            Permanently delete{" "}
            <Text span fw={600}>
              {deleteTarget ? sourceLabel(deleteTarget.filename) : ""}
            </Text>
            ? This removes the file, generated questions, chat history, and cached responses. This cannot be undone.
          </Text>
          <Group justify="flex-end" gap="sm">
            <Button variant="default" onClick={() => setDeleteTarget(null)} disabled={deleteBusy}>
              Cancel
            </Button>
            <Button color="red" loading={deleteBusy} onClick={() => void confirmDeleteSource()}>
              Delete permanently
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={addOpen}
        onClose={() => {
          if (importBusy) {
            uploadAbortRef.current?.abort();
            uploadInFlight.current = false;
            setImportBusy(false);
            setUploadProgress(null);
          }
          setAddOpen(false);
        }}
        title="Add to library"
        size="lg"
        fullScreen={isMobile}
        centered
        overlayProps={MODAL_OVERLAY_PROPS}
        closeOnClickOutside={!importBusy}
        closeOnEscape={!importBusy}
        padding={isMobile ? "md" : undefined}
      >
        <Text size="sm" c="dimmed" mb="lg">
          Upload a file, import a link, or paste notes to generate practice questions.
        </Text>
        <Tabs
          value={addSourceTab}
          onChange={(value) => value && setAddSourceTab(value as AddSourceTab)}
          keepMounted={false}
        >
          <Tabs.List grow mb="md">
            <Tabs.Tab value="file" leftSection={<IconUpload size={16} />} aria-label="Upload a file">
              <Box visibleFrom="sm" component="span">
                File
              </Box>
            </Tabs.Tab>
            <Tabs.Tab value="url" leftSection={<IconLink size={16} />} aria-label="Import from a link">
              <Box visibleFrom="sm" component="span">
                Link
              </Box>
            </Tabs.Tab>
            <Tabs.Tab value="paste" leftSection={<IconFileText size={16} />} aria-label="Paste text">
              <Box visibleFrom="sm" component="span">
                Paste
              </Box>
            </Tabs.Tab>
            <Tabs.Tab value="github" leftSection={<IconLink size={16} />} aria-label="Import a GitHub repository">
              <Box visibleFrom="sm" component="span">
                GitHub
              </Box>
            </Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="file" pt="md">
            <Dropzone
              onDrop={(files) => files[0] && void uploadFile(files[0])}
              accept={[MIME_TYPES.pdf, MIME_TYPES.doc, MIME_TYPES.docx, "text/plain", "text/markdown", "image/*"]}
              loading={importBusy}
              maxFiles={1}
              radius="md"
            >
              {importBusy ? (
                <Stack align="center" justify="center" gap="sm" mih={{ base: 160, sm: 220 }} px="md">
                  <Progress value={uploadProgress ?? 0} size="sm" w="100%" maw={280} animated={uploadProgress === null} />
                  <Text size="sm" c="dimmed">
                    {uploadProgress !== null ? `Uploading… ${uploadProgress}%` : "Uploading…"}
                  </Text>
                </Stack>
              ) : (
                <Group justify="center" gap="xl" mih={{ base: 160, sm: 220 }} style={{ pointerEvents: "none" }}>
                  <Dropzone.Accept>
                    <IconUpload size={52} stroke={1.5} />
                  </Dropzone.Accept>
                  <Dropzone.Reject>
                    <IconX size={52} stroke={1.5} />
                  </Dropzone.Reject>
                  <Dropzone.Idle>
                    <Stack align="center" gap="xs">
                      <IconUpload size={52} stroke={1.5} />
                      <Text size="sm" fw={500}>
                        Drop a file here or click to browse
                      </Text>
                      <Text size="xs" c="dimmed">
                        PDF, Word, text, or image
                      </Text>
                    </Stack>
                  </Dropzone.Idle>
                </Group>
              )}
            </Dropzone>
          </Tabs.Panel>

          <Tabs.Panel value="url" pt="md">
            <Stack>
              <TextInput
                label="Public URL"
                placeholder="https://…"
                value={importUrl}
                onChange={(e) => setImportUrl(e.currentTarget.value)}
                disabled={importBusy}
              />
              <Button
                fullWidth
                loading={importBusy}
                disabled={!importUrl.trim()}
                onClick={() => void runImport("url")}
              >
                Import URL
              </Button>
            </Stack>
          </Tabs.Panel>

          <Tabs.Panel value="paste" pt="md">
            <Stack>
              <Textarea
                label="Pasted text"
                placeholder="Paste article or notes…"
                value={importPaste}
                onChange={(e) => setImportPaste(e.currentTarget.value)}
                disabled={importBusy}
                minRows={6}
              />
              <Button
                fullWidth
                loading={importBusy}
                disabled={!importPaste.trim()}
                onClick={() => void runImport("paste")}
              >
                Use pasted text
              </Button>
            </Stack>
          </Tabs.Panel>

          <Tabs.Panel value="github" pt="md">
            <Stack>
              <TextInput
                label="GitHub repository"
                placeholder="https://github.com/owner/repo"
                value={importGithub}
                onChange={(e) => setImportGithub(e.currentTarget.value)}
                disabled={importBusy}
              />
              <Button
                fullWidth
                loading={importBusy}
                disabled={!importGithub.trim()}
                onClick={() => void runImport("github")}
              >
                Import repository
              </Button>
            </Stack>
          </Tabs.Panel>
        </Tabs>
      </Modal>

      <Modal
        opened={authOpen}
        onClose={() => setAuthOpen(false)}
        title={authMode === "login" ? "Sign in" : "Create account"}
        size="sm"
        centered
        overlayProps={MODAL_OVERLAY_PROPS}
      >
        <Stack>
          <TextInput label="Username" autoComplete="username" {...authForm.getInputProps("username")} />
          <PasswordInput
            label="Password"
            autoComplete={authMode === "login" ? "current-password" : "new-password"}
            {...authForm.getInputProps("password")}
          />
          {authError && (
            <Text size="sm" c="red">
              {authError}
            </Text>
          )}
          <Button
            loading={authSubmitting}
            onClick={() => {
              if (authForm.validate().hasErrors) return;
              void submitAuth(authForm.getValues());
            }}
          >
            {authMode === "login" ? "Sign in" : "Sign up"}
          </Button>
          <Button variant="subtle" size="compact-sm" onClick={() => setAuthMode(authMode === "login" ? "signup" : "login")}>
            {authMode === "login" ? "Need an account? Sign up" : "Have an account? Sign in"}
          </Button>
        </Stack>
      </Modal>
    </WorkspaceShellContext.Provider>
  );
}
