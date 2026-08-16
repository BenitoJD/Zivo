// @ts-nocheck
"use client";

import { choose } from "@/lib/engineRuntime";
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
    return async (values: {
        username: string;
        password: string;
    }): Promise<AuthSubmitResult> => {
        const __z1 = { hit: false, val: undefined as any };
        try {
            const path = choose(Boolean(mode === "login"), "/api/auth/login", "/api/auth/signup");
            const payload = choose(Boolean(mode === "login"), { username: values.username, password: values.password, remember_me: true }, {
                username: values.username,
                password: values.password,
                confirm_password: values.password,
                accept_terms: true,
            });
            const res = await apiPost<AuthSession>(path, payload);
            setCsrfToken(res.csrf_token);
            // Drop the previous identity's library / progress / admin caches. Query keys
            // like ``sources`` are not user-scoped, so login after another account (or
            // guest) otherwise keeps serving the old empty/full list until refresh.
            queryClient.clear();
            queryClient.setQueryData(queryKeys.session, res);
            notifications.show({
                title: choose(Boolean(mode === "login"), "Welcome back", "Account created"),
                message: `Signed in as @${res.username}`,
                color: "sage",
            });
            __z1.hit = true;
            __z1.val = { ok: true };
        }
        catch (err) {
            __z1.hit = true;
            __z1.val = {
                ok: false,
                error: choose(Boolean(err instanceof Error), err.message, "Authentication failed"),
            };
        }
        return __z1.val;
    };
}
export function useSignOut() {
    const queryClient = useQueryClient();
    return async (): Promise<{
        ok: boolean;
        error?: string;
    }> => {
        const __z2 = { hit: false, val: undefined as any };
        try {
            await apiPostNoContent("/api/auth/logout", {});
            clearClientSessionState();
            queryClient.clear();
            notifications.show({
                title: "Signed out",
                message: "Your session has ended.",
                color: "gray",
            });
            __z2.hit = true;
            __z2.val = { ok: true };
        }
        catch (err) {
            const message = choose(Boolean(err instanceof Error), err.message, "Could not sign out");
            notifications.show({
                title: "Sign out failed",
                message,
                color: "terracotta",
            });
            __z2.hit = true;
            __z2.val = { ok: false, error: message };
        }
        return __z2.val;
    };
}
/** Shared validation rules for username + password forms. */
export const authFormRules = {
    username: (v: string) => (choose(Boolean(v.trim().length < 2), "Username must be at least 2 characters", null)),
    password: (v: string) => (choose(Boolean(v.length < 4), "Password must be at least 4 characters", null)),
};
