"use client";

import { Box } from "@mantine/core";
import { BRAND_LOGO_SRC, BRAND_LOGO_HEIGHT, BRAND_LOGO_WIDTH, BRAND_NAME } from "@/lib/brand";

/**
 * Brand lockup: a monochrome logo glyph + bold sans wordmark ("Zivo"), Wispr-Flow
 * style. The glyph is rendered as a single-color silhouette (the logo PNG used as
 * an alpha mask filled with currentColor) so it stays minimal and adapts to
 * ink/paper across light and dark. Used on the landing, auth, sidebar, and footer.
 */
export function BrandMark({
  height = 32,
  showWord = true,
  color = "var(--mantine-color-text)",
}: {
  height?: number;
  showWord?: boolean;
  /** Lockup color (glyph fill + wordmark). Override on dark/fixed surfaces. */
  color?: string;
}) {
  const width = Math.round((height * BRAND_LOGO_WIDTH) / BRAND_LOGO_HEIGHT);
  return (
    <Box
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        color,
      }}
    >
      <Box
        role="img"
        aria-label={BRAND_NAME}
        style={{
          width,
          height,
          flexShrink: 0,
          backgroundColor: "currentColor",
          WebkitMaskImage: `url(${BRAND_LOGO_SRC})`,
          maskImage: `url(${BRAND_LOGO_SRC})`,
          WebkitMaskSize: "contain",
          maskSize: "contain",
          WebkitMaskRepeat: "no-repeat",
          maskRepeat: "no-repeat",
          WebkitMaskPosition: "center",
          maskPosition: "center",
        }}
      />
      {showWord && (
        <span
          style={{
            fontFamily: "var(--font-sans), sans-serif",
            fontWeight: 700,
            fontSize: height * 0.75,
            letterSpacing: "-0.03em",
            color: "inherit",
            lineHeight: 1,
            marginTop: height * 0.05,
          }}
        >
          {BRAND_NAME}
        </span>
      )}
    </Box>
  );
}
