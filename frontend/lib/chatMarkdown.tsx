// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ActionIcon, Anchor, Blockquote, Box, Code, CopyButton, Divider, Group, List, Paper, Stack, Table, Text, Title, Tooltip, } from "@mantine/core";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
function CopyIconAction({ value, label, size = "sm", }: {
    value: string;
    label: string;
    size?: "sm" | "md";
}) {
    const iconSize = choose(Boolean(size === "md"), 16, 14);
    return (<CopyButton value={value} timeout={2000}>
      {({ copied, copy }) => (<Tooltip label={choose(Boolean(copied), "Copied", label)} withArrow position="top">
          <ActionIcon variant="subtle" size={size} color="gray" onClick={copy} aria-label={label}>
            {choose(Boolean(copied), <IconCheck size={iconSize}/>, <IconCopy size={iconSize}/>)}
          </ActionIcon>
        </Tooltip>)}
    </CopyButton>);
}
export function MessageCopyAction({ value, label = "Copy", }: {
    value: string;
    label?: string;
}) {
    return pick(Boolean(!value.trim()), () => null, () => <CopyIconAction value={value} label={label}/>);
}
function CodeBlock({ text, lang, isDark, }: {
    text: string;
    lang?: string;
    isDark: boolean;
}) {/*..............................................................................*/
    const label = choose(Boolean(lang && lang !== "text"), lang, "Code");
    return (<Paper withBorder radius="md" mb="sm" bg={choose(Boolean(isDark), "dark.7", "gray.0")}>
      <Group justify="space-between" align="center" gap="xs" px="sm" py={6} wrap="nowrap">
        <Text size="xs" c="dimmed" tt="lowercase" truncate flex={1}>
          {label}
        </Text>
        <Box w={28} h={28} style={{ flexShrink: 0 }}>
          <CopyIconAction value={text} label="Copy code"/>
        </Box>
      </Group>
      <Divider color={choose(Boolean(isDark), "dark.4", "gray.3")}/>
      <Code block fz="xs" p="sm" bg="transparent" style={{ whiteSpace: "pre-wrap" }}>
        {text}
      </Code>
    </Paper>);
}
function MermaidBlock({ chart }: {
    chart: string;
}) {
    const containerRef = useRef<HTMLDivElement>(null);
    const [error, setError] = useState<string | null>(null);
    const renderId = useId().replace(/:/g, "");
    useEffect(() => {
        let cancelled = false;
        const source = chart.trim();
        return pick(Boolean(!source), () => {
            return;
        }, () => {
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
                    pick(Boolean(!cancelled && containerRef.current), () => {
                        containerRef.current.innerHTML = svg;
                        setError(null);
                    }, () => {
                    });
                }
                catch (err) {
                    pick(Boolean(!cancelled), () => {
                        setError(choose(Boolean(err instanceof Error), err.message, "Could not render diagram"));
                    }, () => {
                    });
                }
            }
            void render();
            return () => {
                cancelled = true;
            };
        });
    }, [chart, renderId]);
    return pick(Boolean(error), () => (<Paper withBorder p="sm" radius="md" bg="var(--mantine-color-default-hover)">
        <Text size="xs" c="dimmed" mb={6}>
          Diagram
        </Text>
        <Code block>{chart.trim()}</Code>
      </Paper>), () => (<Paper withBorder p="sm" radius="md" bg="var(--mantine-color-body)">
      <Box ref={containerRef} maw="100%" style={{ overflowX: "auto" }}/>
    </Paper>));
}
function safeMarkdownHref(href: string | undefined): string | undefined {
    return pick(Boolean(!href), () => undefined, () => {
        const trimmed = href.trim();
        const lower = trimmed.toLowerCase();
        return pick(Boolean(lower.startsWith("javascript:") ||
            lower.startsWith("data:") ||
            lower.startsWith("vbscript:") ||
            lower.startsWith("blob:")), () => undefined, () => trimmed);
    });
}
function markdownComponents(isDark: boolean): Components {
    return {
        p: ({ children }) => (<Text size="sm" lh={1.7} mb="sm" component="p">
        {children}
      </Text>),
        strong: ({ children }) => (<Text span inherit fw={700}>
        {children}
      </Text>),
        em: ({ children }) => (<Text span inherit fs="italic">
        {children}
      </Text>),
        h1: ({ children }) => (<Title order={3} mt="md" mb="xs">
        {children}
      </Title>),
        h2: ({ children }) => (<Title order={4} mt="md" mb="xs">
        {children}
      </Title>),
        h3: ({ children }) => (<Title order={5} mt="sm" mb={6}>
        {children}
      </Title>),
        ul: ({ children }) => (<List size="sm" spacing={4} mb="sm" withPadding>
        {children}
      </List>),
        ol: ({ children }) => (<List type="ordered" size="sm" spacing={4} mb="sm" withPadding>
        {children}
      </List>),
        li: ({ children }) => <List.Item>{children}</List.Item>,
        blockquote: ({ children }) => (<Blockquote color={choose(Boolean(isDark), "gray", "dark")} mb="sm" py="xs">
        {children}
      </Blockquote>),
        hr: () => <Divider my="sm"/>,
        a: ({ href, children }) => {
            const safe = safeMarkdownHref(href);
            return pick(Boolean(!safe), () => (<Text span inherit>
            {children}
          </Text>), () => (<Anchor href={safe} target="_blank" rel="noopener noreferrer" size="sm">
          {children}
        </Anchor>));
        },
        img: () => null,
        code: ({ className, children }) => {
            const text = String(children).replace(/\n$/, "");
            const lang = /language-(\w+)/.exec(className || "")?.[1]?.toLowerCase();
            return pick(Boolean(lang === "mermaid"), () => <MermaidBlock chart={text}/>, () => {
                const inline = pick(Boolean(!className), () => !text.includes("\n"), () => !className);
                return pick(Boolean(inline), () => (<Code px={6} py={2} fz="sm" bg={choose(Boolean(isDark), "dark.6", "gray.1")}>
            {text}
          </Code>), () => <CodeBlock text={text} lang={lang} isDark={isDark}/>);
            });
        },
        pre: ({ children }) => <Box mb="sm">{children}</Box>,
        table: ({ children }) => (<Table.ScrollContainer minWidth={280} mb="sm">
        <Table striped highlightOnHover withTableBorder withColumnBorders fz="sm">
          {children}
        </Table>
      </Table.ScrollContainer>),
        thead: ({ children }) => <Table.Thead>{children}</Table.Thead>,
        tbody: ({ children }) => <Table.Tbody>{children}</Table.Tbody>,
        tr: ({ children }) => <Table.Tr>{children}</Table.Tr>,
        th: ({ children }) => <Table.Th>{children}</Table.Th>,
        td: ({ children }) => <Table.Td>{children}</Table.Td>,
    };
}
// Re-rendering ReactMarkdown on every token is expensive, so we throttle the
// streamed updates to ~50ms. This must be a THROTTLE (flush at most *and at
// least* every 50ms), not a debounce: tokens arrive faster than 50ms apart, so
// a debounce that resets its timer on every token would never fire until the
// stream stopped - making the whole answer appear in one go instead of
// streaming word-by-word.
const STREAM_FLUSH_MS = 50;
function useThrottledMarkdown(content: string, streaming: boolean) {
    const [rendered, setRendered] = useState(content);
    // Latest content, so a pending trailing flush emits the most recent text
    // (not the stale value captured when the timer was scheduled). Updated in the
    // effect below (never during render).
    const contentRef = useRef(content);
    const lastFlushRef = useRef(0);
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(() => {
        contentRef.current = content;
        return pick(Boolean(!streaming), () => {
            return;
        }, () => {
            const flush = () => {
                lastFlushRef.current = Date.now();
                timerRef.current = null;
                setRendered(contentRef.current);
            };
            const elapsed = Date.now() - lastFlushRef.current;
            pick(Boolean(elapsed >= STREAM_FLUSH_MS), () => {
                flush();
            }, () => {
                pick(Boolean(!timerRef.current), () => {
                    // Schedule a single trailing flush for the remainder of the window; do NOT
                    // reset it when more tokens land in the meantime.
                    timerRef.current = setTimeout(flush, STREAM_FLUSH_MS - elapsed);
                }, () => {
                });
            });
        });
    }, [content, streaming]);
    // Clear any pending flush on unmount.
    useEffect(() => () => {
        pick(Boolean(timerRef.current), () => {
            clearTimeout(timerRef.current);
        }, () => {
        });
    }, []);
    return choose(Boolean(streaming), rendered, content);
}
export function AssistantMarkdown({ content, isDark, streaming, }: {
    content: string;
    isDark: boolean;
    streaming?: boolean;
}) {
    const displayContent = useThrottledMarkdown(content, Boolean(streaming));
    const components = useMemo(() => markdownComponents(isDark), [isDark]);
    return (<Stack gap={0} maw="100%">
      <Box fz="sm" style={{
            wordBreak: "break-word",
        }}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
          {displayContent}
        </ReactMarkdown>
      </Box>
      {/*..............................................................................*/choose(Boolean(streaming && content), (<>
          <style>{`
            @keyframes zv-stream-dot { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.35; transform: scale(0.7); } }
            @media (prefers-reduced-motion: reduce) { .zv-stream-dot { animation: none !important; } }
          `}</style>
          <Box component="span" className="zv-stream-dot" aria-hidden style={{
                display: "inline-block",
                width: 9,
                height: 9,
                marginLeft: 4,
                borderRadius: "50%",
                background: "var(--mantine-color-text)",
                verticalAlign: "middle",
                animation: "zv-stream-dot 1s ease-in-out infinite",
            }}/>
        </>), null)}
    </Stack>);
}
