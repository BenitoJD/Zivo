"use client";

import { AuthSplitLayout } from "@/app/_components/AuthSplitLayout";
import { AuthForm } from "@/app/_components/AuthForm";

export default function LoginPage() {
  return (
    <AuthSplitLayout
      eyebrow="Welcome back"
      quote="“The cure for boredom is curiosity. The cure for curiosity is reading.”"
      attribution="- Ellen Parr"
    >
      <AuthForm mode="login" />
    </AuthSplitLayout>
  );
}
