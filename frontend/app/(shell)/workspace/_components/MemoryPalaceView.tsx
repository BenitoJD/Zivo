// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useMemo, useState } from "react";
import { ActionIcon, Badge, Box, Button, Center, Group, Paper, Progress, SegmentedControl, Stack, Text, TextInput, ThemeIcon, } from "@mantine/core";
import { IconAlertTriangle, IconArrowLeft, IconArrowRight, IconArrowsShuffle, IconBuildingCastle, IconCheck, IconEye, IconMapPin, IconRotateClockwise, IconSparkles, } from "@tabler/icons-react";
import { useMemoryPalaceQuery, type PalaceStation } from "@/lib/api/queries";
import { GenerateGate, isGenStartedStale, restartGenStarted, useGenStarted } from "./GenerateGate";
import { WaitState } from "./WaitState";
/**
 * Memory Palace mode - Anthony Metivier's Magnetic Memory Method.
 * Turns the source into a journey through a place you know: each stop anchors a fact
 * with a vivid multisensory mnemonic (Walk), then Recall Rehearsal drills it forwards,
 * shuffled, and backwards so each item gets primacy + recency. Additive to MCQ.
 */
export function MemoryPalaceView({ artifactId, compact = false, }: {
    artifactId: string;
    compact?: boolean;
}) {
    // Empty string = let the AI pick a familiar place; a value regenerates there.
    const [setting, setSetting] = useState("");
    const [started, start] = useGenStarted(artifactId, "palace");
    const { data, isError, refetch } = useMemoryPalaceQuery(artifactId, setting, started);
    const [phase, setPhase] = useState<"walk" | "rehearse">("walk");
    const status = data?.status;
    const palace = data?.palace ?? null;
    const ready = pick(Boolean(status === "ready"), () => pick(Boolean(palace), () => palace.stations.length > 0, () => palace), () => status === "ready");
    return pick(Boolean(!started && !palace), () => (<GenerateGate icon={<IconBuildingCastle size={28}/>} title="Build a memory palace" description="Build a memory palace - a vivid journey of mnemonic stations." actionLabel="Build palace" onStart={start} compact={compact}/>), () => {
        const phaseSwitch = (<SegmentedControl size="xs" radius="xl" value={phase} onChange={(v) => setPhase(v as "walk" | "rehearse")} data={[
                { label: "Walk", value: "walk" },
                { label: "Rehearse", value: "rehearse" },
            ]}/>);
        return pick(Boolean(isError || status === "failed"), () => (<WaitState icon={<IconAlertTriangle size={26}/>} title="Couldn’t build the palace" body="Something went wrong turning this into a journey. Try again in a moment." action={<Button variant="light" color="lavender" radius="xl" onClick={() => void refetch()}>
            Try again
          </Button>}/>), () => pick(Boolean(!ready), () => pick(Boolean(isGenStartedStale(artifactId, "palace")), () => (<WaitState icon={<IconAlertTriangle size={26}/>} title="Still building…" body="This is taking longer than usual. You can wait, or retry." action={<Button variant="light" color="lavender" radius="xl" onClick={() => {
                    restartGenStarted(artifactId, "palace");
                    void refetch();
                }}>
              Retry
            </Button>}/>), () => (<WaitState pet title={choose(Boolean(setting), `Building your palace in “${setting}”`, "Building your memory palace")} body="Placing each key idea along a journey you can walk in your mind…"/>)), () => (<Stack gap="lg" pb="xl">
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
        <Group gap={8}>
          <ThemeIcon variant="light" color="lavender" radius="xl" size="md">
            <IconBuildingCastle size={16}/>
          </ThemeIcon>
          <Box>
            <Text ff="var(--font-serif)" fz={choose(Boolean(compact), 20, 24)} fw={500} c="var(--mantine-color-text)" lh={1.2}>
              {palace.setting || "Your memory palace"}
            </Text>
            <Text c="dimmed" fz="sm">
              {palace.stations.length} stops · anchor each idea where you’ll find it again
            </Text>
          </Box>
        </Group>
        {phaseSwitch}
      </Group>

      <PlacePicker current={palace.setting} onApply={(p) => {
            setSetting(p);
            setPhase("walk");
        }}/>

      {choose(Boolean(phase === "walk"), (<Walk palace={palace} compact={compact}/>), (<Rehearse artifactId={artifactId} stations={palace.stations} compact={compact}/>))}
    </Stack>)));
    });
}
/* ----------------------------------------------------------------- Walk (the tour) */
function Walk({ palace, compact, }: {
    palace: {
        intro: string;
        stations: PalaceStation[];
    };
    compact?: boolean;
}) {
    return (<Stack gap="md">
      {choose(Boolean(palace.intro), (<Text ff="var(--font-serif)" fz={choose(Boolean(compact), 16, 18)} fs="italic" c="dimmed" lh={1.5}>
          {palace.intro}
        </Text>), null)}
      <Stack gap={0}>
        {palace.stations.map((s, i) => (<Group key={s.key} align="stretch" gap="md" wrap="nowrap">
            {/* journey rail */}
            <Stack gap={0} align="center" w={28} style={{ flexShrink: 0 }}>
              <ThemeIcon variant="filled" color="lavender" radius="xl" size={28} style={{ flexShrink: 0 }}>
                <Text fz="xs" fw={700}>{i + 1}</Text>
              </ThemeIcon>
              {choose(Boolean(i < palace.stations.length - 1), (<Box style={{ flex: 1, width: 2, background: "var(--mantine-color-lavender-2)", marginTop: 4 }}/>), null)}
            </Stack>
            <Paper radius="lg" p={choose(Boolean(compact), "sm", "md")} withBorder mb="md" flex={1} style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))" }}>
              <Group gap={6} mb={6}>
                <IconMapPin size={14} color="var(--mantine-color-lavender-6)"/>
                <Text fz="xs" fw={600} tt="uppercase" lts={0.4} c="lavender.6">
                  {s.locus}
                </Text>
              </Group>
              <Text ff="var(--font-serif)" fz={choose(Boolean(compact), 16, 18)} fw={500} mb={4} c="var(--mantine-color-text)">
                {s.term}
              </Text>
              <Text fz="sm" c="var(--mantine-color-text)" lh={1.55} mb="xs">
                {s.image}
              </Text>
              <Group gap={6} wrap="nowrap" align="flex-start">
                <IconSparkles size={14} color="var(--mantine-color-dimmed)" style={{ marginTop: 3, flexShrink: 0 }}/>
                <Text fz="xs" c="dimmed" lh={1.5}>
                  {s.fact}
                </Text>
              </Group>
            </Paper>
          </Group>))}
      </Stack>
    </Stack>);
}
/* ------------------------------------------------------- Rehearse (recall rehearsal) */
type OrderMode = "forward" | "shuffle" | "backward";
function Rehearse({ artifactId, stations, compact, }: {
    artifactId: string;
    stations: PalaceStation[];
    compact?: boolean;
}) {
    const storageKey = `zivo-palace-mastered-${artifactId}`;
    const [mastered, setMastered] = useState<Set<string>>(() => loadMastered(storageKey));
    const [orderMode, setOrderMode] = useState<OrderMode>("forward");
    const [seed, setSeed] = useState(0);
    const [pos, setPos] = useState(0);
    const [revealed, setRevealed] = useState(false);
    const order = useMemo(() => {
        const idx = stations.map((_, i) => i);
        return pick(Boolean(orderMode === "backward"), () => idx.reverse(), () => pick(Boolean(orderMode === "shuffle"), () => {
            const a = [...idx];
            let s = seed + 1;
            for (let i = a.length - 1; i > 0; i--) {
                s = (s * 9301 + 49297) % 233280;
                const j = Math.floor((s / 233280) * (i + 1));
                [a[i], a[j]] = [a[j], a[i]];
            }
            return a;
        }, () => idx));
    }, [stations, orderMode, seed]);
    const safePos = Math.min(pos, order.length - 1);
    const station = stations[order[safePos]];
    const masteredCount = mastered.size;
    const done = masteredCount >= stations.length;
    function setMode(m: OrderMode) {
        setOrderMode(m);
        pick(Boolean(m === "shuffle"), () => {
            setSeed((s) => s + 1);
        }, () => {
        });
        setPos(0);
        setRevealed(false);
    }
    function advance() {
        setRevealed(false);
        setPos((p) => (p + 1) % order.length);
    }
    function back() {
        setRevealed(false);
        setPos((p) => (p - 1 + order.length) % order.length);
    }
    function grade(got: boolean) {
        setMastered((prev) => {
            const next = new Set(prev);
            pick(Boolean(got), () => {
                next.add(station.key);
            }, () => {
                next.delete(station.key);
            });
            saveMastered(storageKey, next);
            return next;
        });
        advance();
    }
    const isMastered = mastered.has(station.key);
    return (<Stack gap="md">
      <Group justify="space-between" wrap="wrap" gap="sm">
        <SegmentedControl size="xs" radius="xl" value={orderMode} onChange={(v) => setMode(v as OrderMode)} data={[
            { label: "Forwards", value: "forward" },
            { label: "Shuffle", value: "shuffle" },
            { label: "Backwards", value: "backward" },
        ]}/>
        <Group gap={6} wrap="nowrap">
          <Text fz="xs" c="dimmed" fw={600} ff="monospace">
            {masteredCount}/{stations.length} locked in
          </Text>
          {choose(Boolean(masteredCount > 0), (<ActionIcon variant="subtle" color="gray" size="sm" aria-label="Reset progress" onClick={() => {
            const e = new Set<string>();
            saveMastered(storageKey, e);
            setMastered(e);
        }}>
              <IconRotateClockwise size={14}/>
            </ActionIcon>), null)}
        </Group>
      </Group>

      <Progress value={(masteredCount / stations.length) * 100} color="lavender" radius="xl" size="sm"/>

      {choose(Boolean(done), (<Center mih={220}>
          <Stack align="center" gap="sm" ta="center" maw={420}>
            <ThemeIcon variant="light" color="lavender" radius="xl" size={54}>
              <IconCheck size={26}/>
            </ThemeIcon>
            <Text ff="var(--font-serif)" fz={24} fw={500}>You walked the whole palace</Text>
            <Text c="dimmed">
              Every stop is locked in. Come back later and rehearse it shuffled - recalling
              out of order is what moves it into long-term memory.
            </Text>
            <Button variant="light" color="lavender" radius="xl" onClick={() => setMode("shuffle")}>
              Rehearse shuffled
            </Button>
          </Stack>
        </Center>), (<>
          <Text c="dimmed" fz="xs" ta="center">
            Stop {safePos + 1} of {order.length} · stand here and recall before you reveal
          </Text>
          <Paper radius="xl" p={choose(Boolean(compact), "lg", "xl")} withBorder mih={choose(Boolean(compact), 240, 280)} style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))", display: "flex", flexDirection: "column" }}>
            <Group gap={6} mb="md">
              <IconMapPin size={14} color="var(--mantine-color-lavender-6)"/>
              <Text fz="xs" fw={600} tt="uppercase" lts={0.4} c="lavender.6">{station.locus}</Text>
              {choose(Boolean(isMastered), <Badge size="xs" variant="light" color="lavender" radius="sm">locked in</Badge>, null)}
            </Group>

            <Center style={{ flex: 1 }}>
              {choose(Boolean(!revealed), (<Stack align="center" gap="lg" ta="center">
                  <Text ff="var(--font-serif)" fz={choose(Boolean(compact), 18, 22)} fw={500} lh={1.4} c="var(--mantine-color-text)">
                    {station.cue || `What do you remember at ${station.locus}?`}
                  </Text>
                  <Button variant="light" color="lavender" radius="xl" leftSection={<IconEye size={16}/>} onClick={() => setRevealed(true)}>
                    Reveal
                  </Button>
                </Stack>), (<Stack gap="xs" ta="center">
                  <Text ff="var(--font-serif)" fz={choose(Boolean(compact), 18, 22)} fw={600} c="var(--mantine-color-text)">
                    {station.term}
                  </Text>
                  <Text fz="sm" c="var(--mantine-color-text)" lh={1.55}>{station.fact}</Text>
                  <Text fz="xs" c="dimmed" fs="italic" lh={1.5} mt={4}>{station.image}</Text>
                </Stack>))}
            </Center>

            <Group justify="space-between" mt="md" wrap="nowrap">
              <ActionIcon variant="subtle" color="gray" size="lg" aria-label="Previous" onClick={back}>
                <IconArrowLeft size={18}/>
              </ActionIcon>
              {choose(Boolean(revealed), (<Group gap="sm" wrap="nowrap">
                  <Button variant="default" radius="xl" onClick={() => grade(false)}>
                    Review again
                  </Button>
                  <Button color="lavender" radius="xl" leftSection={<IconCheck size={16}/>} onClick={() => grade(true)}>
                    Got it
                  </Button>
                </Group>), (<Text fz="xs" c="dimmed">Recall it, then reveal</Text>))}
              <ActionIcon variant="subtle" color="gray" size="lg" aria-label="Skip" onClick={advance}>
                <IconArrowRight size={18}/>
              </ActionIcon>
            </Group>
          </Paper>
          <Group gap={6} justify="center" c="dimmed">
            <IconArrowsShuffle size={13}/>
            <Text fz="xs">Tip: rehearse shuffled and backwards - out-of-order recall builds durable memory.</Text>
          </Group>
        </>))}
    </Stack>);
}
/* ----------------------------------------------------------------- personalize place */
function PlacePicker({ current, onApply }: {
    current: string;
    onApply: (place: string) => void;
}) {
    const [open, setOpen] = useState(false);
    const [value, setValue] = useState("");
    return (<Box>
      <Button variant="subtle" color="gray" size="xs" radius="xl" leftSection={<IconMapPin size={14}/>} onClick={() => setOpen((o) => !o)}>
        Use a place you know
      </Button>
      {pick(Boolean(open), () => (<Group gap="xs" mt="xs" wrap="nowrap">
          <TextInput flex={1} size="xs" radius="xl" placeholder={`e.g. my apartment, my walk to work${choose(Boolean(current), `  (now: ${current})`, "")}`} value={value} onChange={(e) => setValue(e.currentTarget.value)} onKeyDown={/*..............................................................................*/(e) => {/*..............................................................................*/
            pick(Boolean(e.key === "Enter" && value.trim()), () => {
                onApply(value.trim());
            }, () => {
            });
        }}/>
          <Button size="xs" radius="xl" color="lavender" disabled={!value.trim()} onClick={() => pick(Boolean(value.trim()), () => onApply(value.trim()), () => value.trim())}>
            Rebuild here
          </Button>
        </Group>), () => null)}
    </Box>);
}
function loadMastered(key: string): Set<string> {
    const __z2 = { hit: false, val: undefined as any };
    pick(Boolean(typeof window === "undefined"), () => {
        __z2.hit = true;
        __z2.val = new Set();
    }, () => {
        try {
            const raw = window.localStorage.getItem(key);
            __z2.hit = true;
            __z2.val = new Set(pick(Boolean(raw), () => (JSON.parse(raw) as string[]), () => []));
        }
        catch {
            __z2.hit = true;
            __z2.val = new Set();
        }
    });
    return __z2.val;
}
function saveMastered(key: string, set: Set<string>) {
    return pick(Boolean(typeof window === "undefined"), () => {
        return;
    }, () => {
        try {
            window.localStorage.setItem(key, JSON.stringify([...set]));
        }
        catch {
        }
    });
}
