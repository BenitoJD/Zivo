"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  ActionIcon,
  Anchor,
  Blockquote,
  Box,
  Code,
  CopyButton,
  Divider,
  Group,
  List,
  Paper,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

function CopyIconAction({
  value,
  label,
  size = "sm",
}: {
  value: string;
  label: string;
  size?: "sm" | "md";
}) {
  const iconSize = size === "md" ? 16 : 14;
  return (
    <CopyButton value={value} timeout={2000}>
      {({ copied, copy }) => (
        <Tooltip label={copied ? "Copied" : label} withArrow position="top">
          <ActionIcon
            variant="subtle"
            size={size}
            color="gray"
            onClick={copy}
            aria-label={label}
          >
            {copied ? <IconCheck size={iconSize} /> : <IconCopy size={iconSize} />}
          </ActionIcon>
        </Tooltip>
      )}
    </CopyButton>
  );
}

export function MessageCopyAction({
  value,
  label = "Copy",
}: {
  value: string;
  label?: string;
}) {
  if (!value.trim()) return null;
  return <CopyIconAction value={value} label={label} />;
}

function CodeBlock({
  text,
  lang,
  isDark,
}: {
  text: string;
  lang?: string;
  isDark: boolean;
}) {
  const label = lang && lang !== "text" ? lang : "Code";
  return (
    <Paper withBorder radius="md" mb="sm" bg={isDark ? "dark.7" : "gray.0"}>
      <Group
        justify="space-between"
        align="center"
        gap="xs"
        px="sm"
        py={6}
        wrap="nowrap"
      >
        <Text size="xs" c="dimmed" tt="lowercase" truncate>
          {label}
        </Text>
        <CopyIconAction value={text} label="Copy code" />
      </Group>
      <Divider color={isDark ? "dark.4" : "gray.3"} />
      <Code block fz="xs" p="sm" bg="transparent" style={{ whiteSpace: "pre-wrap" }}>
        {text}
      </Code>
    </Paper>
  );
}

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
      return <CodeBlock text={text} lang={lang} isDark={isDark} />;
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

function useThrottledMarkdown(content: string, streaming: boolean) {
  const [rendered, setRendered] = useState(content);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!streaming) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setRendered(content), 50);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [content, streaming]);

  return streaming ? rendered : content;
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
  const displayContent = useThrottledMarkdown(content, Boolean(streaming));
  const components = useMemo(() => markdownComponents(isDark), [isDark]);

  return (
    <Stack gap={0} maw="100%">
      <Box
        fz="sm"
        style={{
          wordBreak: "break-word",
        }}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
          {displayContent}
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
