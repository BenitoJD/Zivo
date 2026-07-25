"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Box, Button, Checkbox, Stack, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { AuthSplitLayout } from "@/app/_components/AuthSplitLayout";
import { apiGet, apiPost, setCsrfToken } from "@/lib/api/client";
import { authFormRules } from "@/lib/auth";
import { queryKeys, type AuthSession } from "@/lib/api/queries";

/**
 * First-time Google sign-up: OAuth already verified identity; user only picks
 * a public username before we mint the normal zivo_session cookie.
 */
export default function GoogleSignupPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const form = useForm({
    initialValues: { username: "", accept_terms: false },
    validate: {
      username: authFormRules.username,
      accept_terms: (v: boolean) => (v ? null : "Accept the terms to continue"),
    },
  });

  useEffect(() => {
    let cancelled = false;
    void apiGet<{ pending: boolean; email?: string }>("/api/auth/google/pending")
      .then((res) => {
        if (cancelled) return;
        if (!res.pending) {
          router.replace("/login?error=" + encodeURIComponent("Google signup expired — try again"));
          return;
        }
        setEmail(res.email ?? null);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) {
          router.replace("/login?error=" + encodeURIComponent("Google signup expired — try again"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function onSubmit() {
    if (form.validate().hasErrors) return;
    setSubmitting(true);
    setError("");
    try {
      const res = await apiPost<AuthSession>("/api/auth/google/complete", {
        username: form.values.username,
        accept_terms: form.values.accept_terms,
      });
      setCsrfToken(res.csrf_token);
      queryClient.clear();
      queryClient.setQueryData(queryKeys.session, res);
      notifications.show({
        title: "Account created",
        message: `Signed in as @${res.username}`,
        color: "sage",
      });
      router.replace("/workspace");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not finish signup");
      setSubmitting(false);
    }
  }

  return (
    <AuthSplitLayout
      eyebrow="Almost there"
      quote="“A name is the blueprint of the thing we call character.”"
      attribution="- Anonymous"
    >
      <Stack gap="md">
        <Stack gap={4}>
          <Text size="sm" fw={600} tt="uppercase" lts={2} c="lavender.7">
            Choose a username
          </Text>
          <Text size="xs" c="gray.6">
            {email
              ? `Google verified ${email}. Pick the public handle others will see.`
              : "Pick the public handle others will see."}
          </Text>
        </Stack>

        {loading ? (
          <Text size="sm" c="gray.6">
            Checking Google signup…
          </Text>
        ) : (
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
              <Checkbox
                label="I accept the terms of use"
                {...form.getInputProps("accept_terms", { type: "checkbox" })}
              />
              {error && (
                <Text size="sm" c="terracotta.7" fw={500}>
                  {error}
                </Text>
              )}
              <Button type="submit" size="md" loading={submitting} fullWidth>
                Create account
              </Button>
            </Stack>
          </Box>
        )}
      </Stack>
    </AuthSplitLayout>
  );
}
