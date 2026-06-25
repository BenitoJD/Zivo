"use client";

import Image from "next/image";
import { Box, Title } from "@mantine/core";
import { BRAND_LOGO_SRC, BRAND_LOGO_HEIGHT, BRAND_LOGO_WIDTH, BRAND_NAME } from "@/lib/brand";

/**
 * Brand wordmark: logo glyph + serif wordmark. Used across the front door
 * (landing, auth) and available to the shell. Sized by the logo height.
 */
export function BrandMark({
  height = 32,
  showWord = true,
}: {
  height?: number;
  showWord?: boolean;
}) {
  const width = Math.round((height * BRAND_LOGO_WIDTH) / BRAND_LOGO_HEIGHT);
  return (
    <Box style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
      <Image
        src={BRAND_LOGO_SRC}
        alt={BRAND_NAME}
        width={width}
        height={height}
        priority
        unoptimized
        style={{ width, height, flexShrink: 0, objectFit: "contain", display: "block" }}
      />
      {showWord && (
        <Title
          order={4}
          style={{ letterSpacing: "-0.03em", fontFamily: "var(--font-serif), Georgia, serif" }}
        >
          {BRAND_NAME}
        </Title>
      )}
    </Box>
  );
}
