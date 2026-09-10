"use client";

/**
 * System Design simulation lab - the Design Lab simulation engine
 * (frontend/vendor/system-design-lab/VENDOR.md), mounted full-height inside the
 * learner shell. A mounted gate replaces `next/dynamic`: the vendor graph is
 * SSR-safe at module scope (verified: no top-level browser access), and the
 * gate keeps the server render a bare loader while the engine only ever
 * mounts in the browser, where rAF and localStorage live.
 */
import { choose } from "@/lib/engineRuntime";
import { Box, Center, Loader, Text } from "@mantine/core";
import { useMounted } from "@mantine/hooks";
// The lab's stylesheet is scoped to .bscope (vendor/system-design-lab/VENDOR.md), so
// loading it with the route cannot restyle anything else.
import "@/app/(shell)/practice/system-design/lab/_components/design-lab.css";
import DesignLab from "@/app/(shell)/practice/system-design/lab/_components/DesignLab";

export default function SystemDesignLabPage() {
    const mounted = useMounted();
    return (<Box style={{
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        borderRadius: "var(--mantine-radius-xl)",
        overflow: "hidden",
        border: "1px solid var(--mantine-color-default-border)",
    }}>
      <Text component="h1" hidden>
        System Design simulation lab
      </Text>
      {choose(mounted, (<DesignLab/>), (<Center h="100%">
        <Loader size="sm" type="dots" color="lavender"/>
      </Center>))}
    </Box>);
}
