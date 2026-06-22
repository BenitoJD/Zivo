"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  Anchor,
  Blockquote,
  Box,
  Code,
  Divider,
  List,
  Paper,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

function MermaidBlock({ chart }: { chart: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const renderId = useId().replace(/:/g, "");

  useEffect(() => {
    let cancelled = false;
    const source = chart.trim();
    if (!source) return;

    async function render() {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "neutral",
          securityLevel: "strict",
          fontFamily: "var(--mantine-font-family)",
        });
        const { svg } = await mermaid.render(`zivo-mermaid-${renderId}`, source);
        if (!cancelled && containerRef.current) {
          containerRef.current.innerHTML = svg;
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not render diagram");
        }
      }
    }

    void render();
    return () => {
      cancelled = true;
    };
  }, [chart, renderId]);

  if (error) {
    return (
      <Paper withBorder p="sm" radius="md" bg="var(--mantine-color-default-hover)">
        <Text size="xs" c="dimmed" mb={6}>
          Diagram
        </Text>
        <Code block>{chart.trim()}</Code>
      </Paper>
    );
  }

  return (
    <Paper withBorder p="sm" radius="md" bg="var(--mantine-color-body)">
      <Box ref={containerRef} maw="100%" style={{ overflowX: "auto" }} />
    </Paper>
  );
}

function markdownComponents(isDark: boolean): Components {
  return {
    p: ({ children }) => (
      <Text size="sm" lh={1.7} mb="sm" component="p">
        {children}
      </Text>
    ),
    strong: ({ children }) => (
      <Text span inherit fw={700}>
        {children}
      </Text>
    ),
    em: ({ children }) => (
      <Text span inherit fs="italic">
        {children}
      </Text>
    ),
    h1: ({ children }) => (
      <Title order={3} mt="md" mb="xs">
        {children}
      </Title>
    ),
    h2: ({ children }) => (
      <Title order={4} mt="md" mb="xs">
        {children}
      </Title>
    ),
    h3: ({ children }) => (
      <Title order={5} mt="sm" mb={6}>
        {children}
      </Title>
    ),
    ul: ({ children }) => (
      <List size="sm" spacing={4} mb="sm" withPadding>
        {children}
      </List>
    ),
    ol: ({ children }) => (
      <List type="ordered" size="sm" spacing={4} mb="sm" withPadding>
        {children}
      </List>
    ),
    li: ({ children }) => <List.Item>{children}</List.Item>,
    blockquote: ({ children }) => (
      <Blockquote color={isDark ? "gray" : "dark"} mb="sm" py="xs">
        {children}
      </Blockquote>
    ),
    hr: () => <Divider my="sm" />,
    a: ({ href, children }) => (
      <Anchor href={href} target="_blank" rel="noopener noreferrer" size="sm">
        {children}
      </Anchor>
    ),
    code: ({ className, children }) => {
      const text = String(children).replace(/\n$/, "");
      const lang = /language-(\w+)/.exec(className || "")?.[1]?.toLowerCase();
      if (lang === "mermaid") {
        return <MermaidBlock chart={text} />;
      }
      const inline = !className && !text.includes("\n");
      if (inline) {
        return (
          <Code
            px={6}
            py={2}
            fz="sm"
            bg={isDark ? "dark.6" : "gray.1"}
          >
            {text}
          </Code>
        );
      }
      return (
        <Paper withBorder p="sm" radius="md" mb="sm" bg={isDark ? "dark.7" : "gray.0"}>
          <Code block fz="xs" style={{ whiteSpace: "pre-wrap" }}>
            {text}
          </Code>
        </Paper>
      );
    },
    pre: ({ children }) => <Box mb="sm">{children}</Box>,
    table: ({ children }) => (
      <Table.ScrollContainer minWidth={280} mb="sm">
        <Table striped highlightOnHover withTableBorder withColumnBorders fz="sm">
          {children}
        </Table>
      </Table.ScrollContainer>
    ),
    thead: ({ children }) => <Table.Thead>{children}</Table.Thead>,
    tbody: ({ children }) => <Table.Tbody>{children}</Table.Tbody>,
    tr: ({ children }) => <Table.Tr>{children}</Table.Tr>,
    th: ({ children }) => <Table.Th>{children}</Table.Th>,
    td: ({ children }) => <Table.Td>{children}</Table.Td>,
  };
}

export function AssistantMarkdown({
  content,
  isDark,
  streaming,
}: {
  content: string;
  isDark: boolean;
  streaming?: boolean;
}) {
  return (
    <Stack gap={0} maw="100%">
      <Box
        fz="sm"
        style={{
          wordBreak: "break-word",
        }}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents(isDark)}>
          {content}
        </ReactMarkdown>
      </Box>
      {streaming && content ? (
        <Text span size="sm" c="dimmed" component="span">
          ▍
        </Text>
      ) : null}
    </Stack>
  );
}
