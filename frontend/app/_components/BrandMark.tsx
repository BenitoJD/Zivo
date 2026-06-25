"use client";

import Image from "next/image";
import { Box, Title } from "@mantine/core";
import { BRAND_LOGO_SRC, BRAND_LOGO_HEIGHT, BRAND_LOGO_WIDTH, BRAND_NAME } from "@/lib/brand";

/**
 * Brand lockup: logo glyph + serif wordmark ("Zivo", title-case, EB Garamond 500,
 * tightened tracking). Used across the front door (landing, auth), the sidebar,
 * the mobile header, and the footer. Sized by the logo height.
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
    <Box
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        color: "var(--mantine-color-text)",
      }}
    >
      <Image
        src={BRAND_LOGO_SRC}
        alt={BRAND_NAME}
        width={width}
        height={height}
        priority
        unoptimized
        style={{
          width,
          height,
          flexShrink: 0,
          objectFit: "contain",
          display: "block",
        }}
      />
      {showWord && (
        <Title
          order={4}
          className="serif-text"
          style={{
            fontWeight: 500,
            letterSpacing: "-0.02em",
            color: "inherit",
          }}
        >
          {BRAND_NAME}
        </Title>
      )}
    </Box>
  );
}
