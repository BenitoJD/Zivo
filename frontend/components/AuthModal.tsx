"use client";

import { useEffect, useState } from "react";
import { apiPost, setCsrfToken } from "@/lib/api/client";

type AuthModalProps = {
  open: boolean;
  onClose: () => void;
  onSuccess: (username: string) => void;
};

export function AuthModal({ open, onClose, onSuccess }: AuthModalProps) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const path = mode === "login" ? "/api/auth/login" : "/api/auth/signup";
      const payload =
        mode === "login"
          ? { username, password, remember_me: false }
          : {
              username,
              password,
              confirm_password: password,
              accept_terms: true,
            };
      const res = await apiPost<{ username: string; csrf_token: string }>(path, payload);
      setCsrfToken(res.csrf_token);
      onSuccess(res.username);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Auth failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal auth-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="auth-title">{mode === "login" ? "Sign in" : "Create account"}</h2>
        <form onSubmit={submit} className="auth-modal__form">
          <label>
            Username
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              required
            />
          </label>
          {error && <p className="auth-modal__error">{error}</p>}
          <button type="submit" disabled={submitting}>
            {submitting ? "…" : mode === "login" ? "Sign in" : "Sign up"}
          </button>
        </form>
        <button
          type="button"
          className="auth-modal__toggle"
          onClick={() => setMode(mode === "login" ? "signup" : "login")}
        >
          {mode === "login" ? "Need an account? Sign up" : "Have an account? Sign in"}
        </button>
      </div>
    </div>
  );
}
