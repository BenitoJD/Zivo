// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
/**
 * Concept page - detail for a single Wikidata concept + auto-generation trigger.
 *
 * When a visitor lands on a concept with zero questions, we fire on-demand
 * generation (fetch Wikipedia → run the generator) and poll until questions
 * appear. This is the "library is never empty" promise.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Anchor, Button, Center, Container, Group, Paper, Progress, Stack, Text, Title, } from "@mantine/core";
import { IconArrowLeft, IconArrowRight } from "@tabler/icons-react";
import { apiGet, apiPost, ensureGuestSession } from "@/lib/api/client";
import { Shell } from "@/app/practice/_components/Shell";
type ConceptSummary = {
    qid: string;
    label: string;
    description: string;
};
type ConceptDetail = {
    qid: string;
    label: string;
    description: string;
    question_count: number;
    is_generating: boolean;
    parents: ConceptSummary[];
    children: ConceptSummary[];
};
type GenerateResponse = {
    status: string; // "ready" | "generating" | "empty"
    qid: string;
    label: string;
    question_count: number;
    job_id?: string | null;
    error?: string | null;
};
const POLL_INTERVAL_MS = 2500;
const MAX_POLL_ATTEMPTS = 60; // ~2.5 min cap
export default function ConceptPage({ params }: {
    params: Promise<{
        qid: string;
    }>;
}) {
    const router = useRouter();
    const [qid, setQid] = useState<string>("");
    const [detail, setDetail] = useState<ConceptDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [generating, setGenerating] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // Guard against firing generation more than once for a concept. A ref (not
    // state) avoids the cascading-render smell the linter flags on setState-in-effect.
    const generateRequestedFor = useRef<string | null>(null);
    useEffect(() => {
        void (async () => {
            const { qid: q } = await params;
            setQid(q);
        })();
    }, [params]);
    const loadDetail = useCallback(async () => {
        const __z1 = { hit: false, val: undefined as any };
        await pick(Boolean(!qid), async () => {
            __z1.hit = true;
            __z1.val = null;
        }, async () => {
            try {
                const data = await apiGet<ConceptDetail>(`/api/practice/concepts/${qid}`);
                setDetail(data);
                __z1.hit = true;
                __z1.val = data;
            }
            catch {
                setError("Could not load this concept. Please try again.");
                __z1.hit = true;
                __z1.val = null;
            }
        });
        return __z1.val;
    }, [qid]);
    // Initial load + guest bootstrap.
    useEffect(() => {
        return pick(Boolean(!qid), () => {
            return;
        }, () => {
            let cancelled = false;
            void ensureGuestSession().then(() => {
                return pick(Boolean(cancelled), () => {
                    return;
                }, () => {
                    setLoading(true);
                    void loadDetail().finally(() => {
                        pick(Boolean(!cancelled), () => {
                            setLoading(false);
                        }, () => {
                        });
                    });
                });
            });
            return () => {
                cancelled = true;
            };
        });
    }, [qid, loadDetail]);
    // Auto-trigger generation when the concept is empty (once per concept).
    const triggerGenerate = useCallback(async () => {
        const __z2 = { hit: false, val: undefined as any };
        await pick(Boolean(!qid), async () => {
            __z2.hit = true;
        }, async () => {
            setGenerating(true);
            setError(null);
            try {
                const res = await apiPost<GenerateResponse>(`/api/practice/concepts/${qid}/generate`, {});
                await pick(Boolean(res.status === "ready" && res.question_count > 0), async () => {
                    setGenerating(false);
                    await loadDetail();
                    __z2.hit = true;
                }, async () => {
                    pick(Boolean(res.error), () => {
                        setError(res.error);
                        setGenerating(false);
                    }, () => {
                    });
                });
            }
            catch {
                setError("Could not start generation. Please try again.");
                setGenerating(false);
            }
        });
    }, [qid, loadDetail]);
    useEffect(() => {
        return pick(Boolean(!detail || detail.question_count > 0 || generating), () => {
            return;
        }, () => pick(Boolean(generateRequestedFor.current === qid), () => {
            return;
        }, () => {
            generateRequestedFor.current = qid;
            // Fire-and-forget; setState happens inside the async callback, not the effect body.
            void triggerGenerate();
        }));
    }, [detail, generating, qid, triggerGenerate]);
    // Poll question count while generating. State updates happen in the timer
    // callback (an external event), which is the recommended effect usage.
    useEffect(() => {
        return pick(Boolean(!generating || !qid), () => {
            return;
        }, () => {
            let attempts = 0;
            const timer = setInterval(async () => {
                attempts += 1;
                return await pick(Boolean(attempts >= MAX_POLL_ATTEMPTS), async () => {
                    clearInterval(timer);
                    setError("Generation is taking longer than usual. Please try again in a moment.");
                    setGenerating(false);
                    return;
                }, async () => {/*..............................................................................*/
                    const fresh = await loadDetail();
                    pick(Boolean(fresh && fresh.question_count > 0), () => {
                        clearInterval(timer);
                        setGenerating(false);
                    }, () => {
                    });
                });
            }, POLL_INTERVAL_MS);
            return () => clearInterval(timer);
        });
    }, [generating, qid, loadDetail]);
    return pick(Boolean(loading), () => (<Shell>
        <Center style={{ height: "60vh" }}>
          <Stack align="center" gap="sm">
            <Progress value={100} size="sm" radius="xl" w={160} animated color="lavender"/>
            <Text size="sm" c="dimmed">
              Loading concept…
            </Text>
          </Stack>
        </Center>
      </Shell>), () => pick(Boolean(error && !detail), () => (<Shell>
        <Center style={{ height: "60vh" }}>
          <Stack align="center" gap="md">
            <Text c="terracotta.7">{error}</Text>
            <Button variant="light" onClick={() => router.push("/practice")}>
              Back to search
            </Button>
          </Stack>
        </Center>
      </Shell>), () => {
        const label = detail?.label || qid;
        const description = detail?.description || "";
        const count = detail?.question_count ?? 0;
        return (<Shell>
      <Container size="md" py={{ base: 32, md: 48 }}>
        <Stack gap="xl">
          {/* Breadcrumbs / back */}
          <Anchor href="/practice" size="sm" c="gray.6" underline="hover">
            <Group gap={6}>
              <IconArrowLeft size={14}/>
              Practice library
            </Group>
          </Anchor>

          {/* Concept header */}
          <Stack gap="xs">
            {pick(Boolean(detail), () => pick(Boolean(detail.parents.length > 0), () => (<Text size="sm" c="dimmed">
                {detail.parents.map((p) => p.label).join(" › ")}
              </Text>), () => detail.parents.length > 0), () => detail)}
            <Title order={1} fw={500} style={{ fontFamily: "var(--font-serif), Georgia, serif", letterSpacing: "-0.02em" }}>
              {label}
            </Title>
            {pick(Boolean(description), () => (<Text size="md" c="gray.6" lh={1.6} maw={640}>
                {description}
              </Text>), () => description)}
            <Text size="sm" c="lavender.7" fw={500}>
              {choose(Boolean(count > 0), `${count} practice question${choose(Boolean(count === 1), "", "s")}`, choose(Boolean(generating), "Generating questions…", "No questions yet"))}
            </Text>
          </Stack>

          {/* Generating, empty, or ready */}
          {choose(Boolean(generating), (<Paper shadow="paper" radius="xl" p="xl" withBorder bg="gray.0">
              <Stack align="center" gap="md" py="lg">
                <Progress value={100} size="sm" radius="xl" w={200} animated color="lavender"/>
                <Stack align="center" gap={4}>
                  <Text size="lg" fw={500} ta="center">
                    Generating practice questions for {label}
                  </Text>
                  <Text size="sm" c="dimmed" ta="center" lh={1.55} maw={420}>
                    We&apos;re reading the source material and writing fresh, exam-quality
                    questions with explanations. This usually takes under a minute.
                  </Text>
                </Stack>
                {pick(Boolean(error), () => (<Text size="sm" c="terracotta.7" ta="center">
                    {error}
                  </Text>), () => error)}
              </Stack>
            </Paper>), choose(Boolean(count === 0), (<Paper shadow="paper" radius="xl" p="xl" withBorder bg="gray.0">
              <Stack align="center" gap="md" py="lg">
                <Stack align="center" gap={4}>
                  <Text size="lg" fw={500} ta="center">
                    No questions for {label} yet
                  </Text>
                  <Text size="sm" c="dimmed" ta="center" lh={1.55} maw={420}>
                    Generate a fresh set from the concept&apos;s source material.
                  </Text>
                </Stack>
                {pick(Boolean(error), () => (<Text size="sm" c="terracotta.7" ta="center">
                    {error}
                  </Text>), () => error)}
                <Button radius="xl" color="lavender" onClick={() => {
                    generateRequestedFor.current = null;
                    void triggerGenerate();
                }}>
                  Generate questions
                </Button>
              </Stack>
            </Paper>), (<Stack gap="sm" align="center">
              <Button size="lg" radius="xl" rightSection={<IconArrowRight size={18}/>} onClick={() => router.push(`/practice/c/${qid}/run`)}>
                Start practicing
              </Button>
              <Text size="xs" c="dimmed">
                No sign-up needed · {count} questions ready
              </Text>
            </Stack>)))}

          {/* Children / subtopics */}
          {pick(Boolean(detail), () => pick(Boolean(detail.children.length > 0), () => (<Stack gap="xs">
              <Text size="sm" c="dimmed" fw={500}>
                Subtopics
              </Text>
              {detail.children.map((c) => (<Anchor key={c.qid} href={`/practice/c/${c.qid}`} underline="never">
                  <Paper radius="lg" p="md" shadow="paper" bg="gray.0" withBorder>
                    <Group justify="space-between">
                      <Text fw={500}>{c.label}</Text>
                      <IconArrowRight size={16} color="var(--mantine-color-lavender-7)"/>
                    </Group>
                  </Paper>
                </Anchor>))}
            </Stack>), () => detail.children.length > 0), () => detail)}
        </Stack>
      </Container>
    </Shell>);
    }));
}
