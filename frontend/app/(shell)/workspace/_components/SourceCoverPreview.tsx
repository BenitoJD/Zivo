// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Box, HoverCard, Loader, Stack, Text } from "@mantine/core";
import { IconFileText } from "@tabler/icons-react";
import { apiFetchBytes } from "@/lib/api/client";
import { loadPdfForArtifact, renderPdfThumbToCanvas } from "@/lib/pdf";
import type { SourceDocument } from "@/lib/types";
const CHIP_SIZE = 32;
const PEEK_WIDTH = 132;
const PEEK_HEIGHT = 184;
function sourceLabel(filename: string) {
    return filename.replace(/\.[^.]+$/, "");
}
export function isPdfSource(doc: Pick<SourceDocument, "filename" | "content_type">): boolean {
    const name = doc.filename.toLowerCase();
    return doc.content_type === "application/pdf" || name.endsWith(".pdf");
}
type CoverPhase = "idle" | "loading" | "ready" | "error";
type CoverContextValue = {
    doc: SourceDocument;
    label: string;
    isPdf: boolean;
    phase: CoverPhase;
    chipRef: React.RefObject<HTMLCanvasElement | null>;
    peekRef: React.RefObject<HTMLCanvasElement | null>;
};
const CoverContext = createContext<CoverContextValue | null>(null);
function useCoverContext(): CoverContextValue {
    const ctx = useContext(CoverContext);
    pick(Boolean(!ctx), () => {
        throw new Error("SourceCover components must be used inside SourceCoverHover");
    }, () => {
    });
    return ctx;
}
function useSourceCover(doc: SourceDocument, eager: boolean) {
    const isPdf = isPdfSource(doc);
    const [phase, setPhase] = useState<CoverPhase>("idle");
    const chipRef = useRef<HTMLCanvasElement>(null);
    const peekRef = useRef<HTMLCanvasElement>(null);
    const startedRef = useRef(false);
    useEffect(() => {
        return pick(Boolean(!eager || !isPdf || startedRef.current), () => {
            return;
        }, () => {
            startedRef.current = true;
            let cancelled = false;
            setPhase("loading");
            void (async () => {
                const __z1 = { hit: false, val: undefined as any };
                try {
                    const pdf = await loadPdfForArtifact(doc.id, {
                        url: `/api/documents/${doc.id}/file`,
                        fetchBytes: () => apiFetchBytes(`/api/documents/${doc.id}/file`),
                    });
                    await pick(Boolean(cancelled), async () => {
                        __z1.hit = true;
                    }, async () => {
                        const chipCanvas = chipRef.current;
                        const peekCanvas = peekRef.current;
                        await pick(Boolean(chipCanvas), async () => {
                            await renderPdfThumbToCanvas(pdf, 1, chipCanvas, CHIP_SIZE, CHIP_SIZE);
                        }, async () => {
                        });
                        await pick(Boolean(peekCanvas), async () => {
                            await renderPdfThumbToCanvas(pdf, 1, peekCanvas, PEEK_WIDTH, PEEK_HEIGHT);
                        }, async () => {
                        });
                        pick(Boolean(!cancelled), () => {
                            setPhase("ready");
                        }, () => {
                        });
                    });
                }
                catch {
                    pick(Boolean(!cancelled), () => {
                        setPhase("error");
                    }, () => {
                    });
                }
            })();
            return () => {
                cancelled = true;
            };
        });
    }, [doc.id, eager, isPdf]);
    return {
        doc,
        label: sourceLabel(doc.filename),
        isPdf,
        phase,
        chipRef,
        peekRef,
    };
}
/** Stable tint from filename — a quiet typographic cover for non-PDF sources. */
function coverTint(filename: string): {
    bg: string;
    ink: string;
} {
    let hash = 0;
    for (let i = 0; i < filename.length; i += 1) {
        hash = (hash * 31 + filename.charCodeAt(i)) | 0;
    }
    const hues = [
        { bg: "var(--mantine-color-lavender-1)", ink: "var(--mantine-color-lavender-8)" },
        { bg: "var(--mantine-color-sage-1)", ink: "var(--mantine-color-sage-8)" },
        { bg: "var(--mantine-color-gray-2)", ink: "var(--mantine-color-dark-6)" },
        { bg: "#F5E9DC", ink: "#5C4033" },
        { bg: "#E8EEF5", ink: "#2C3E50" },
    ];
    return hues[Math.abs(hash) % hues.length];
}
function TypographicCover({ label, size, serif = false, }: {
    label: string;
    size: "chip" | "peek";
    serif?: boolean;
}) {
    const letter = (label.trim()[0] ?? "?").toUpperCase();
    const tint = coverTint(label);
    const isChip = size === "chip";
    return (<Box aria-hidden style={{
            width: choose(Boolean(isChip), CHIP_SIZE, PEEK_WIDTH),
            height: choose(Boolean(isChip), CHIP_SIZE, PEEK_HEIGHT),
            borderRadius: choose(Boolean(isChip), 10, 6),
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: tint.bg,
            color: tint.ink,
            fontFamily: choose(Boolean(serif), "var(--font-serif)", undefined),
            fontSize: choose(Boolean(isChip), 14, 52),
            fontWeight: 500,
            fontStyle: choose(Boolean(serif), "italic", undefined),
            lineHeight: 1,
            userSelect: "none",
        }}>
      {letter}
    </Box>);
}
function CoverCanvas({ canvasRef, width, height, visible, radius, }: {
    canvasRef: React.RefObject<HTMLCanvasElement | null>;
    width: number;
    height: number;
    visible: boolean;
    radius: number;
}) {
    return (<canvas ref={canvasRef} aria-hidden style={{
            position: "absolute",
            inset: 0,
            width,
            height,
            borderRadius: radius,
            opacity: choose(Boolean(visible), 1, 0),
            transition: "opacity 220ms cubic-bezier(0.32, 0.72, 0, 1)",
            display: "block",
        }}/>);
}
export function SourceCoverChip() {
    const { label, isPdf, phase, chipRef } = useCoverContext();
    const showCanvas = pick(Boolean(isPdf), () => phase === "ready", () => isPdf);
    return pick(Boolean(!isPdf), () => <TypographicCover label={label} size="chip"/>, () => (<Box pos="relative" w={CHIP_SIZE} h={CHIP_SIZE} style={{ flexShrink: 0 }}>
      <Box aria-hidden style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            opacity: choose(Boolean(showCanvas), 0, 1),
            transition: "opacity 220ms cubic-bezier(0.32, 0.72, 0, 1)",
            color: "var(--mantine-color-dimmed)",
        }}>
        {choose(Boolean(phase === "loading"), <Loader size={14} color="lavender"/>, <IconFileText size={16} stroke={1.7}/>)}
      </Box>
      <CoverCanvas canvasRef={chipRef} width={CHIP_SIZE} height={CHIP_SIZE} visible={showCanvas} radius={10}/>
    </Box>));
}
function SourceCoverPeekBody() {
    const { doc, label, isPdf, phase, peekRef } = useCoverContext();
    return (<Stack gap={10} p={4} w={PEEK_WIDTH + 8}>
      <Box className="zivo-cover-peek-frame">
        {choose(Boolean(isPdf), (<>
            <Box aria-hidden style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "var(--mantine-color-gray-0)",
            }}>
              {pick(Boolean(phase === "loading"), () => <Loader size="sm" color="lavender"/>, () => phase === "loading")}
              {pick(Boolean(phase === "error"), () => <IconFileText size={28} stroke={1.4} color="var(--mantine-color-dimmed)"/>, () => phase === "error")}
            </Box>
            <CoverCanvas canvasRef={peekRef} width={PEEK_WIDTH} height={PEEK_HEIGHT} visible={phase === "ready"} radius={6}/>
            <span className="zivo-cover-spine" aria-hidden/>
          </>), (<TypographicCover label={label} size="peek" serif/>))}
      </Box>
      <Stack gap={2} px={2}>
        <Text size="sm" fw={500} lh={1.35} lineClamp={2} ff="var(--font-serif)">
          {label}
        </Text>
        <Text size="xs" c="dimmed" tt="capitalize">
          {pick(Boolean(isPdf), () => "PDF", () => doc.content_type?.split("/").pop() ?? "Document")}
        </Text>
      </Stack>
    </Stack>);
}
const COVER_PEEK_STYLES = `
  .zivo-cover-peek-frame {
    position: relative;
    width: ${PEEK_WIDTH}px;
    height: ${PEEK_HEIGHT}px;
    border-radius: 6px;
    overflow: hidden;
    background: #fff;
    box-shadow:
      0 1px 2px rgba(35, 34, 32, 0.06),
      0 8px 24px rgba(35, 34, 32, 0.12),
      inset 0 0 0 1px rgba(35, 34, 32, 0.06);
  }
  [data-mantine-color-scheme="dark"] .zivo-cover-peek-frame {
    background: var(--mantine-color-dark-5);
    box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.2),
      0 10px 28px rgba(0, 0, 0, 0.45),
      inset 0 0 0 1px rgba(255, 255, 255, 0.06);
  }
  .zivo-cover-spine {
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 7px;
    background: linear-gradient(90deg, rgba(0, 0, 0, 0.14), rgba(0, 0, 0, 0.04) 55%, transparent);
    pointer-events: none;
    z-index: 2;
  }
  [data-mantine-color-scheme="dark"] .zivo-cover-spine {
    background: linear-gradient(90deg, rgba(0, 0, 0, 0.35), rgba(0, 0, 0, 0.12) 55%, transparent);
  }
  .zivo-cover-dropdown {
    padding: 10px !important;
    border: 1px solid var(--mantine-color-default-border) !important;
    background: var(--mantine-color-body) !important;
    animation: zivo-cover-in 260ms cubic-bezier(0.32, 0.72, 0, 1);
  }
  @keyframes zivo-cover-in {
    from { opacity: 0; transform: translateX(-6px) scale(0.97); }
    to { opacity: 1; transform: translateX(0) scale(1); }
  }
  @media (prefers-reduced-motion: reduce) {
    .zivo-cover-dropdown { animation: none !important; }
  }
`;
/**
 * Wraps a source row with a bookshelf-style cover peek on hover.
 * PDFs render page 1; other types get a calm typographic cover.
 */
export function SourceCoverHover({ doc, children, disabled = false, }: {
    doc: SourceDocument;
    children: ReactNode;
    disabled?: boolean;
}) {
    const [eager, setEager] = useState(false);
    const cover = useSourceCover(doc, eager);
    return pick(Boolean(disabled), () => <>{children}</>, () => (<CoverContext.Provider value={cover}>
      <style>{COVER_PEEK_STYLES}</style>
      <HoverCard width={PEEK_WIDTH + 28} position="right" offset={14} openDelay={180} closeDelay={80} shadow="paper-lg" radius="lg" withinPortal onOpen={() => setEager(true)}>
        <HoverCard.Target>{children}</HoverCard.Target>
        <HoverCard.Dropdown className="zivo-cover-dropdown">
          <SourceCoverPeekBody />
        </HoverCard.Dropdown>
      </HoverCard>
    </CoverContext.Provider>));
}
