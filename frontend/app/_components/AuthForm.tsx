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
import { useSubmitAuth, authFormRules, type AuthMode } from "@/lib/auth";
import { apiGet, apiUrl } from "@/lib/api/client";

/** Official multicolor Google “G” — Tabler IconBrandGoogle is mono currentColor. */
function GoogleMark({ size = 18 }: { size?: number }) {
  return (
    <Box
      component="svg"
      w={size}
      h={size}
      display="block"
      viewBox="0 0 48 48"
      aria-hidden
    >
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </Box>
  );
}

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
            leftSection={<GoogleMark size={18} />}
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

      <Group justify="space-between" wrap="wrap" gap="sm">
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
