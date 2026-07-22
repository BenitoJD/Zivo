"use client";

import { AuthSplitLayout } from "@/app/_components/AuthSplitLayout";
import { AuthForm } from "@/app/_components/AuthForm";

export default function SignupPage() {
  return (
    <AuthSplitLayout
      eyebrow="Start studying"
      quote="“The important thing is not to stop questioning. Curiosity has its own reason for existing.”"
      attribution="- Albert Einstein"
    >
      <AuthForm mode="signup" />
    </AuthSplitLayout>
  );
}
