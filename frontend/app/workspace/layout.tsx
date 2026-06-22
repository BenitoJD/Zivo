"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  AppShell,
  Burger,
  Button,
  Group,
  Modal,
  NavLink,
  PasswordInput,
  Progress,
  ScrollArea,
  Stack,
  Switch,
  Tabs,
  Text,
  Textarea,
  TextInput,
  Title,
  useMantineColorScheme,
} from "@mantine/core";
import { Dropzone, MIME_TYPES } from "@mantine/dropzone";
import { useForm } from "@mantine/form";
import { useDisclosure, useMediaQuery, useMounted } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconFileText, IconLink, IconLogin, IconMoon, IconSun, IconUpload, IconX } from "@tabler/icons-react";
import { apiGet, apiPost, apiPostForm, ensureGuestSession, isArtifactId, setCsrfToken } from "@/lib/api/client";
import { STORAGE_LIMIT_BYTES } from "@/lib/constants";
import type { SourceDocument } from "@/lib/types";

/** Frosted backdrop for modals. */
const MODAL_OVERLAY_PROPS = {
  backgroundOpacity: 0.55,
  blur: 4,
} as const;

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

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const artifactId = pathname.startsWith("/workspace/") ? pathname.split("/")[2] : undefined;
  const [opened, { toggle }] = useDisclosure();
  const isMobile = useMediaQuery("(max-width: 48em)");
  const mounted = useMounted();
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const isDark = mounted ? colorScheme === "dark" : true;

  const [addOpen, setAddOpen] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [username, setUsername] = useState<string | null>(null);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);

  const [importUrl, setImportUrl] = useState("");
  const [importPaste, setImportPaste] = useState("");
  const [importGithub, setImportGithub] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [addSourceTab, setAddSourceTab] = useState<AddSourceTab>("file");

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
    try {
      setDocuments(await apiGet<SourceDocument[]>("/api/sources"));
    } catch {
      /* empty */
    }
  }, []);

  useEffect(() => {
    void ensureGuestSession();
    apiGet<{ username: string; csrf_token: string }>("/api/auth/session")
      .then((s) => {
        setUsername(s.username);
        setCsrfToken(s.csrf_token);
      })
      .catch(() => setUsername(null));
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiGet<SourceDocument[]>("/api/sources")
      .then((docs) => {
        if (!cancelled) setDocuments(docs);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (documents.some((d) => d.status === "indexing")) void loadDocs();
    }, 4000);
    return () => window.clearInterval(id);
  }, [documents, loadDocs]);

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
    setImportBusy(true);
    try {
      await ensureGuestSession();
      const form = new FormData();
      form.append("file", file);
      const data = await apiPostForm<{ id: string }>("/api/sources", form);
      notifications.show({ title: "Uploaded", message: file.name, color: "green" });
      setImportUrl("");
      setImportPaste("");
      setImportGithub("");
      onUploaded(data.id);
      setAddOpen(false);
    } catch (e) {
      notifications.show({
        title: "Upload failed",
        message: e instanceof Error ? e.message : "Unknown error",
        color: "red",
      });
    } finally {
      setImportBusy(false);
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
      const res = await apiPost<{ username: string; csrf_token: string }>(path, payload);
      setCsrfToken(res.csrf_token);
      setUsername(res.username);
      authForm.reset();
      setAuthOpen(false);
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Auth failed");
    } finally {
      setAuthSubmitting(false);
    }
  }

  return (
    <WorkspaceShellContext.Provider value={{ openAddSource: () => setAddOpen(true) }}>
      <AppShell
        header={{ height: { base: 48, sm: 0 } }}
        navbar={{ width: 280, breakpoint: "sm", collapsed: { mobile: !opened } }}
        padding="md"
      >
        <AppShell.Header hiddenFrom="sm">
          <Group h="100%" px="md">
            <Burger opened={opened} onClick={toggle} size="sm" aria-label="Open menu" />
            <Title order={4}>Question Better.</Title>
          </Group>
        </AppShell.Header>
        <AppShell.Navbar>
          <AppShell.Section p="md" visibleFrom="sm">
            <Title order={4}>Question Better.</Title>
          </AppShell.Section>
          <AppShell.Section grow p="xs">
            <ScrollArea h="100%" type="auto">
              {documents.length === 0 ? (
                <Text size="sm" c="dimmed" px="sm">
                  No sources yet. Add a PDF or article to start.
                </Text>
              ) : (
                documents.map((d) => (
                  <NavLink
                    key={d.id}
                    label={d.filename.replace(/\.[^.]+$/, "")}
                    description={
                      d.status === "indexing"
                        ? `Indexing ${d.index_progress}%`
                        : d.status === "pending"
                          ? "Choose pages"
                          : d.status
                    }
                    leftSection={<IconFileText size={16} />}
                    active={artifactId === d.id}
                    onClick={() => {
                      router.push(`/workspace/${d.id}`);
                      if (isMobile) toggle();
                    }}
                  />
                ))
              )}
            </ScrollArea>
          </AppShell.Section>
          <AppShell.Section p="md">
            {username && (
              <Stack gap="xs" mb="md">
                <Group justify="space-between">
                  <Text size="xs" c="dimmed">
                    Storage
                  </Text>
                  <Text size="xs">{storagePct}%</Text>
                </Group>
                <Progress value={storagePct} size="sm" />
              </Stack>
            )}
            <Switch
              size="md"
              checked={isDark}
              onChange={() => toggleColorScheme()}
              onLabel={<IconMoon size={14} stroke={2.5} />}
              offLabel={<IconSun size={14} stroke={2.5} />}
              label={isDark ? "Dark mode" : "Light mode"}
              labelPosition="left"
              aria-label={isDark ? "Dark mode on. Switch to light mode" : "Light mode on. Switch to dark mode"}
              mb="sm"
              styles={{
                body: { justifyContent: "space-between", width: "100%" },
              }}
            />
            <Button
              fullWidth
              variant="white"
              c="dark.9"
              leftSection={<IconUpload size={16} />}
              onClick={() => setAddOpen(true)}
              mb="sm"
            >
              Add source
            </Button>
            <Button variant="subtle" fullWidth leftSection={<IconLogin size={16} />} onClick={() => setAuthOpen(true)}>
              {username ? `@${username}` : "Sign in"}
            </Button>
          </AppShell.Section>
        </AppShell.Navbar>
        <AppShell.Main>{children}</AppShell.Main>
      </AppShell>

      <Modal
        opened={addOpen}
        onClose={() => !importBusy && setAddOpen(false)}
        title="Add to library"
        size="lg"
        centered
        overlayProps={MODAL_OVERLAY_PROPS}
        closeOnClickOutside={!importBusy}
        closeOnEscape={!importBusy}
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
            <Tabs.Tab value="file" leftSection={<IconUpload size={16} />}>
              File
            </Tabs.Tab>
            <Tabs.Tab value="url" leftSection={<IconLink size={16} />}>
              Link
            </Tabs.Tab>
            <Tabs.Tab value="paste" leftSection={<IconFileText size={16} />}>
              Paste
            </Tabs.Tab>
            <Tabs.Tab value="github" leftSection={<IconLink size={16} />}>
              GitHub
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
              <Group justify="center" gap="xl" mih={220} style={{ pointerEvents: "none" }}>
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
