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
} from "@/app/workspace/_components/Sidebar";
import { AddSourceModal } from "@/app/workspace/_components/AddSourceModal";
import { DeleteSourceModal } from "@/app/workspace/_components/DeleteSourceModal";
import { sourceLabel } from "@/app/workspace/_components/Sidebar";
import { SettingsModal } from "@/app/workspace/_components/SettingsModal";
import { OnboardingGuide } from "@/app/workspace/_components/OnboardingGuide";

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
  const isMobile = useMediaQuery("(max-width: 48em)", false, { getInitialValueInEffect: true });
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

  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<SourceDocument | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(false);

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
    <WorkspaceShellContext.Provider value={{ openAddSource: () => setAddOpen(true) }}>
      <AppShell
        transitionDuration={reduceMotion ? 0 : 280}
        transitionTimingFunction="cubic-bezier(0.32, 0.72, 0, 1)"
        header={{ height: { base: MOBILE_HEADER_HEIGHT, sm: 0 } }}
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
            background: "var(--mantine-color-gray-0)",
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
        <AppShell.Header hiddenFrom="sm" px="md">
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
                onClick={() => setAddOpen(true)}
                w={34}
                style={{ flexShrink: 0 }}
              >
                <IconUpload size={18} stroke={1.5} />
              </ActionIcon>
            )}
          </Group>
        </AppShell.Header>

        <AppShell.Navbar p={{ base: "sm", sm: "md" }}>
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
            onAddSource={() => {
              setAddOpen(true);
              if (isMobile) closeMobile();
            }}
            onSignIn={() => {
              router.push("/login");
              if (isMobile) closeMobile();
            }}
            onOpenModels={() => {
              router.push("/workspace/models");
              if (isMobile) closeMobile();
            }}
            onDeleteSource={(doc) => setDeleteTarget(doc)}
            onOpenSettings={() => setSettingsOpen(true)}
          />
        </AppShell.Navbar>

        <AppShell.Main>{children}</AppShell.Main>
      </AppShell>

      <AddSourceModal opened={addOpen} onClose={() => setAddOpen(false)} isMobile={Boolean(isMobile)} />
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
