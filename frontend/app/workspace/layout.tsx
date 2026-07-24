"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AppShell, Box, Burger, Group } from "@mantine/core";
import { ActionIcon } from "@mantine/core";
import { IconUpload } from "@tabler/icons-react";
import { useDisclosure, useLocalStorage, useMediaQuery, useMounted } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { ensureGuestSession } from "@/lib/api/client";
import { useInvalidateSources, useSessionQuery, useSourcesQuery } from "@/lib/api/queries";
import { STORAGE_LIMIT_BYTES } from "@/lib/constants";
import type { SourceDocument } from "@/lib/types";
import { BrandMark } from "@/app/_components/BrandMark";
import {
  Sidebar,
  SIDEBAR_EXPANDED_WIDTH,
  SIDEBAR_MINI_WIDTH,
  SHELL_MS,
  SHELL_EASE,
} from "@/app/workspace/_components/Sidebar";
import { DeleteSourceModal } from "@/app/workspace/_components/DeleteSourceModal";
import { AddSourceModal } from "@/app/workspace/_components/AddSourceModal";
import { PomodoroWidget } from "@/app/workspace/_components/PomodoroWidget";
import { sourceLabel } from "@/app/workspace/_components/Sidebar";
import { SettingsModal } from "@/app/workspace/_components/SettingsModal";
import { OnboardingGuide } from "@/app/workspace/_components/OnboardingGuide";
import { StudyNavProvider } from "@/app/workspace/_components/studyNav";
import { MOBILE_MAX_MQ } from "@/lib/responsive";

const MOBILE_HEADER_HEIGHT = 52;

type WorkspaceShellContextValue = { openAddSource: () => void };

const WorkspaceShellContext = createContext<WorkspaceShellContextValue | null>(null);

export function useWorkspaceShell() {
  const ctx = useContext(WorkspaceShellContext);
  if (!ctx) throw new Error("useWorkspaceShell must be used within WorkspaceLayout");
  return ctx;
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
  // Strictly below 48em so it never overlaps AppShell's `sm` navbar breakpoint
  // (which shows the desktop navbar at exactly 768px = iPad portrait); the overlap
  // gave the navbar width:"100%" while rendered inline, pushing content off-screen.
  const isMobile = useMediaQuery(MOBILE_MAX_MQ, false, { getInitialValueInEffect: true });
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)", false, {
    getInitialValueInEffect: true,
  });
  const mounted = useMounted();

  const sidebarWide = mounted ? (isMobile ? mobileOpened : sidebarExpanded) : false;
  const sidebarWidth = isMobile
    ? "100%"
    : mounted && sidebarWide
      ? SIDEBAR_EXPANDED_WIDTH
      : SIDEBAR_MINI_WIDTH;

  const [deleteTarget, setDeleteTarget] = useState<SourceDocument | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [addSourceOpen, setAddSourceOpen] = useState(false);

  function openAddSource() {
    setAddSourceOpen(true);
    if (isMobile) closeMobile();
  }

  useEffect(() => {
    if (typeof window !== "undefined") {
      const completed = localStorage.getItem("zivo-onboarding-completed");
      if (completed !== "true") {
        const t = setTimeout(() => setOnboardingOpen(true), 100);
        return () => clearTimeout(t);
      }
    }
  }, []);

  const invalidateSources = useInvalidateSources();
  const sourcesQuery = useSourcesQuery();
  const sessionQuery = useSessionQuery();
  const documents = sourcesQuery.data ?? [];
  const username = sessionQuery.data?.username ?? null;
  const isAdmin = Boolean(sessionQuery.data?.is_admin);

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  useEffect(() => {
    if (sourcesQuery.error) {
      notifications.show({
        title: "Could not load library",
        message: sourcesQuery.error instanceof Error ? sourcesQuery.error.message : "Unknown error",
        color: "terracotta",
      });
    }
  }, [sourcesQuery.error]);

  useEffect(() => {
    if (isMobile) closeMobile();
  }, [pathname, isMobile, closeMobile]);

  const storagePct = Math.min(
    100,
    Math.round((documents.reduce((s, d) => s + d.size_bytes, 0) / STORAGE_LIMIT_BYTES) * 100),
  );

  function toggleSidebar() {
    if (isMobile) {
      toggleMobile();
      return;
    }
    setSidebarExpanded(!sidebarExpanded);
  }

  function navigateSource(id: string) {
    router.push(`/workspace/${id}`);
    if (isMobile) closeMobile();
  }

  return (
    <WorkspaceShellContext.Provider value={{ openAddSource }}>
      <StudyNavProvider>
      {/* On phones Mantine forces the navbar to 100% width, which stretches the nav
          rows into sparse empty space. Make it a proper drawer instead. */}
      <style>{`
        @media (max-width: 47.99em) {
          .zv-mobile-drawer {
            width: min(86vw, 332px) !important;
            max-width: min(86vw, 332px) !important;
            box-shadow: 8px 0 40px rgba(20, 18, 30, 0.18);
          }
        }
      `}</style>
      <AppShell
        zIndex={120}
        transitionDuration={reduceMotion ? 0 : SHELL_MS}
        transitionTimingFunction={SHELL_EASE}
        header={{
          height: {
            base: `calc(${MOBILE_HEADER_HEIGHT}px + env(safe-area-inset-top, 0px))`,
            sm: 0,
          },
        }}
        navbar={{
          width: sidebarWidth,
          breakpoint: "sm",
          collapsed: { mobile: !mobileOpened, desktop: false },
        }}
        padding={{ base: "xs", sm: "md" }}
        styles={{
          root: { height: "100dvh", overflow: "hidden" },
          header: { borderBottom: "1px solid var(--mantine-color-default-border)" },
          navbar: {
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
            borderRight: "1px solid var(--mantine-color-default-border)",
            background: "var(--mantine-color-body)",
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
        <AppShell.Header
          hiddenFrom="sm"
          px="md"
          style={{ paddingTop: "env(safe-area-inset-top)" }}
        >
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
              <BrandMark height={26} />
            </Group>
            {mobileOpened ? (
              <Box w={34} aria-hidden style={{ flexShrink: 0 }} />
            ) : (
              <ActionIcon
                variant="subtle"
                color="gray"
                aria-label="Add source"
                onClick={openAddSource}
                w={34}
                style={{ flexShrink: 0 }}
              >
                <IconUpload size={18} stroke={1.5} />
              </ActionIcon>
            )}
          </Group>
        </AppShell.Header>

        {/* Horizontal padding lives on the inner sections, not the navbar: the
            collapsed 64px rail pins its layers to the full width, so navbar side
            padding would shove the icons right and clip them. Keep only vertical. */}
        <AppShell.Navbar className="zv-mobile-drawer" px={0} py={{ base: "sm", sm: "md" }}>
          <Sidebar
            documents={documents}
            artifactId={artifactId}
            wide={sidebarWide}
            isMobile={Boolean(isMobile)}
            reduceMotion={Boolean(reduceMotion)}
            username={username}
            isAdmin={isAdmin}
            storagePct={storagePct}
            pathname={pathname}
            onToggleSidebar={toggleSidebar}
            onNavigateSource={navigateSource}
            onAddSource={openAddSource}
            onSignIn={() => {
              router.push("/login");
              if (isMobile) closeMobile();
            }}
            onOpenModels={() => {
              router.push("/workspace/models");
              if (isMobile) closeMobile();
            }}
            onOpenNewspaper={() => {
              router.push("/workspace/newspaper");
              if (isMobile) closeMobile();
            }}
            onOpenNewspaperPractice={() => {
              router.push("/practice/newspaper");
              if (isMobile) closeMobile();
            }}
            onOpenLearnAdmin={() => {
              router.push("/workspace/learn");
              if (isMobile) closeMobile();
            }}
            onOpenProgress={() => {
              router.push("/workspace/progress");
              if (isMobile) closeMobile();
            }}
            onOpenCodingBank={() => {
              router.push("/workspace/coding");
              if (isMobile) closeMobile();
            }}
            onDeleteSource={(doc) => setDeleteTarget(doc)}
            onOpenSettings={() => setSettingsOpen(true)}
          />
        </AppShell.Navbar>

        <AppShell.Main>{children}</AppShell.Main>
      </AppShell>
      {isMobile && mobileOpened ? (
        <Box
          onClick={closeMobile}
          aria-hidden
          style={{ position: "fixed", inset: 0, zIndex: 110, background: "rgba(20, 18, 30, 0.42)" }}
        />
      ) : null}
      </StudyNavProvider>

      <AddSourceModal
        opened={addSourceOpen}
        onClose={() => setAddSourceOpen(false)}
        onImported={(id) => router.push(`/workspace/${id}`)}
      />
      {/* Floating study focus timer (desktop) - bottom-left launcher. */}
      {mounted && !isMobile && <PomodoroWidget />}
      <DeleteSourceModal
        target={deleteTarget}
        onClose={() => setDeleteTarget(null)}
        label={sourceLabel}
        documents={documents}
        onDeleted={() => invalidateSources()}
      />
      <SettingsModal
        opened={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        username={username}
        onReplayOnboarding={() => setOnboardingOpen(true)}
      />
      <OnboardingGuide opened={onboardingOpen} onClose={() => setOnboardingOpen(false)} />
    </WorkspaceShellContext.Provider>
  );
}
