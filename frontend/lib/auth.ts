"use client";

import { useQueryClient } from "@tanstack/react-query";
import { notifications } from "@mantine/notifications";
import { apiPost, apiPostNoContent, clearClientSessionState, setCsrfToken } from "@/lib/api/client";
import { queryKeys, type AuthSession } from "@/lib/api/queries";

export type AuthMode = "login" | "signup";

export type AuthSubmitResult = {
  ok: boolean;
  error?: string;
};

/**
 * Shared auth submit used by both the dedicated /login & /signup pages and the
 * (legacy) shell auth modal. Mirrors the backend contract exactly:
 *  - login:  POST /api/auth/login  { username, password, remember_me }
 *  - signup: POST /api/auth/signup { username, password, confirm_password, accept_terms }
 * On success it stores the CSRF token and seeds the session cache, so every
 * hook consumer (useSessionQuery) sees the new user immediately.
 */
export function useSubmitAuth(mode: AuthMode) {
  const queryClient = useQueryClient();

  return async (values: { username: string; password: string }): Promise<AuthSubmitResult> => {
    try {
      const path = mode === "login" ? "/api/auth/login" : "/api/auth/signup";
      const payload =
        mode === "login"
          ? { username: values.username, password: values.password, remember_me: false }
          : {
              username: values.username,
              password: values.password,
              confirm_password: values.password,
              accept_terms: true,
            };
      const res = await apiPost<AuthSession>(path, payload);
      setCsrfToken(res.csrf_token);
      queryClient.setQueryData(queryKeys.session, res);
      notifications.show({
        title: mode === "login" ? "Welcome back" : "Account created",
        message: `Signed in as @${res.username}`,
        color: "sage",
      });
      return { ok: true };
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : "Authentication failed",
      };
    }
  };
}

export function useSignOut() {
  const queryClient = useQueryClient();

  return async (): Promise<{ ok: boolean; error?: string }> => {
    try {
      await apiPostNoContent("/api/auth/logout", {});
      clearClientSessionState();
      await queryClient.invalidateQueries({ queryKey: queryKeys.session });
      notifications.show({
        title: "Signed out",
        message: "Your session has ended.",
        color: "gray",
      });
      return { ok: true };
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not sign out";
      notifications.show({
        title: "Sign out failed",
        message,
        color: "terracotta",
      });
      return { ok: false, error: message };
    }
  };
}

/** Shared validation rules for username + password forms. */
export const authFormRules = {
  username: (v: string) => (v.trim().length < 2 ? "Username must be at least 2 characters" : null),
  password: (v: string) => (v.length < 4 ? "Password must be at least 4 characters" : null),
};
