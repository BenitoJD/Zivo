"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import {
  Box,
  Button,
  Divider,
  Group,
  PasswordInput,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { IconBrandGoogle } from "@tabler/icons-react";
import { useSubmitAuth, authFormRules, type AuthMode } from "@/lib/auth";
import { apiGet, apiUrl } from "@/lib/api/client";

function AuthFormInner({ mode }: { mode: AuthMode }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const submitAuth = useSubmitAuth(mode);
  const [submitting, setSubmitting] = useState(false);
  const oauthError = searchParams.get("error") ?? "";
  const [error, setError] = useState("");
  const displayError = error || oauthError;
  const [googleEnabled, setGoogleEnabled] = useState(false);
  const form = useForm({
    initialValues: { username: "", password: "" },
    validate: authFormRules,
  });

  useEffect(() => {
    let cancelled = false;
    void apiGet<{ enabled: boolean }>("/api/auth/google/status")
      .then((res) => {
        if (!cancelled) setGoogleEnabled(Boolean(res.enabled));
      })
      .catch(() => {
        if (!cancelled) setGoogleEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSubmit() {
    if (form.validate().hasErrors) return;
    setSubmitting(true);
    setError("");
    const result = await submitAuth(form.getValues());
    setSubmitting(false);
    if (result.ok) {
      router.replace("/workspace");
      return;
    }
    setError(result.error ?? "Something went wrong");
  }

  const isLogin = mode === "login";

  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Text size="sm" fw={600} tt="uppercase" lts={2} c="lavender.7">
          {isLogin ? "Welcome back" : "Get started"}
        </Text>
        <Text size="xs" c="gray.6">
          {isLogin
            ? "Sign in to pick up where you left off."
            : "Create an account to save your sources and progress."}
        </Text>
      </Stack>

      {googleEnabled && (
        <>
          <Button
            component="a"
            href={apiUrl("/api/auth/google")}
            variant="default"
            size="md"
            fullWidth
            leftSection={<IconBrandGoogle size={18} stroke={1.5} />}
          >
            Continue with Google
          </Button>
          <Divider label="or" labelPosition="center" />
        </>
      )}

      <Box
        component="form"
        onSubmit={(e) => {
          e.preventDefault();
          void onSubmit();
        }}
      >
        <Stack gap="md">
          <TextInput
            label="Username"
            placeholder="your-username"
            autoComplete="username"
            size="md"
            {...form.getInputProps("username")}
          />
          <PasswordInput
            label="Password"
            placeholder="••••••••"
            autoComplete={isLogin ? "current-password" : "new-password"}
            size="md"
            {...form.getInputProps("password")}
          />

          {displayError && (
            <Text size="sm" c="terracotta.7" fw={500}>
              {displayError}
            </Text>
          )}

          <Button type="submit" size="md" loading={submitting} fullWidth>
            {isLogin ? "Sign in" : "Create account"}
          </Button>
        </Stack>
      </Box>

      <Group justify="space-between" wrap="nowrap">
        <Text
          size="sm"
          c="lavender.7"
          fw={500}
          component="a"
          href={isLogin ? "/signup" : "/login"}
          style={{ cursor: "pointer", textDecoration: "none" }}
        >
          {isLogin ? "Need an account? Sign up" : "Have an account? Sign in"}
        </Text>
        <Text
          size="sm"
          c="gray.5"
          component="a"
          href="/workspace"
          style={{ cursor: "pointer", textDecoration: "none" }}
        >
          Continue as guest
        </Text>
      </Group>
    </Stack>
  );
}

/**
 * Shared auth form used by /login and /signup. Split-layout brand panel is
 * provided by the page; this is just the form card. On success it routes to
 * /workspace.
 */
export function AuthForm({ mode }: { mode: AuthMode }) {
  return (
    <Suspense fallback={null}>
      <AuthFormInner mode={mode} />
    </Suspense>
  );
}
