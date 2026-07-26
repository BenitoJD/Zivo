"use client";

import { useState } from "react";
import { Box, Paper, Text } from "@mantine/core";
import {
  newspaperBrandVisual,
  newspaperFaviconUrl,
} from "@/lib/newspaperBrands";

type Props = {
  slug: string;
  title: string;
  size?: number;
};

/**
 * Paper tile with the publication favicon (or serif initials on miss).
 */
export function NewspaperBrandMark({ slug, title, size = 52 }: Props) {
  const visual = newspaperBrandVisual(slug, title);
  const [imgFailed, setImgFailed] = useState(false);
  const logoSize = Math.round(size * 0.52);

  return (
    <Paper
      radius="lg"
      withBorder
      bg="gray.0"
      shadow="paper"
      w={size}
      h={size}
      style={{
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
      }}
    >
      {!imgFailed ? (
        <Box
          component="img"
          src={newspaperFaviconUrl(visual.domain, 128)}
          alt=""
          w={logoSize}
          h={logoSize}
          style={{ objectFit: "contain" }}
          onError={() => setImgFailed(true)}
        />
      ) : (
        <Text
          ff="var(--font-serif)"
          fw={600}
          fz={size <= 44 ? "sm" : "md"}
          c={`${visual.accent}.7`}
          lh={1}
        >
          {visual.short}
        </Text>
      )}
    </Paper>
  );
}
