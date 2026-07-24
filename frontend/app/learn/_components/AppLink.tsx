"use client";

/**
 * Next Link + Mantine polymorphic props for Server Component pages.
 * Passing `component={Link}` from an RSC into Mantine clients throws:
 * "Functions cannot be passed directly to Client Components".
 */

import Link from "next/link";
import {
  Anchor,
  Box,
  Button,
  type AnchorProps,
  type BoxProps,
  type ButtonProps,
} from "@mantine/core";
import type { ReactNode } from "react";

type Href = { href: string; children?: ReactNode };

export function LinkAnchor({
  href,
  children,
  ...props
}: Omit<AnchorProps, "component"> & Href) {
  return (
    <Anchor component={Link} href={href} {...props}>
      {children}
    </Anchor>
  );
}

export function LinkButton({
  href,
  children,
  ...props
}: Omit<ButtonProps, "component"> & Href) {
  return (
    <Button component={Link} href={href} {...props}>
      {children}
    </Button>
  );
}

export function LinkBox({
  href,
  children,
  ...props
}: Omit<BoxProps, "component"> & Href) {
  return (
    <Box component={Link} href={href} {...props}>
      {children}
    </Box>
  );
}
