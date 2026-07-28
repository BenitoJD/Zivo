"use client";

import type { ReactNode } from "react";
import { ScrollViewport } from "@/app/_components/ScrollViewport";

/** Scroll surface inside the shared learner AppShell. */
export function Shell({ children }: { children: ReactNode }) {
  return <ScrollViewport variant="flex">{children}</ScrollViewport>;
}
