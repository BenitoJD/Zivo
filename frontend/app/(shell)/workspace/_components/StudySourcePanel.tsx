// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent, } from "react";
import { ActionIcon, Box, Button, Center, Group, Loader, ScrollArea, Stack, Text, ThemeIcon, Title, Tooltip, } from "@mantine/core";
import { IconArrowsMaximize, IconFileText, IconZoomIn, IconZoomOut } from "@tabler/icons-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { cancelAllPdfRenders, clampPdfScroll, pdfDisplayHeight, pdfPageAspectRatio, pdfPageFitScale, renderPdfPageToCanvas, snapPdfZoom, PDF_ZOOM_PRESETS, PDF_DEFAULT_ZOOM, } from "@/lib/pdf";
import { useIsDark } from "@/lib/useIsDark";
/**
 * Study source panel (extracted from the workspace page monolith): the in-session
 * PDF viewer with zoom toolbar (PdfReaderToolbar) and the canvas render stage
 * (SourceStage) - the "source" pane of the 3-pane study view.
 */
export function StudySourcePanel({ filename, pageRange, currentPage, artifactId, isPdf, pdfLoading, pdfError, pdfDoc, studyPages, pageTexts, pageTextsLoading, open, }: {
    filename?: string;
    pageRange?: {
        from: number;
        to: number;
    };
    currentPage?: number;
    artifactId?: string;
    isPdf: boolean;
    pdfLoading: boolean;
    pdfError: string | null;
    pdfDoc: PDFDocumentProxy | null;
    studyPages: number[];
    /** Non-PDF page text keyed by page number (from /api/documents/.../pages). */
    pageTexts?: Record<number, string>;
    pageTextsLoading?: boolean;
    open: boolean;
}) {
    const isDark = useIsDark();
    const [zoom, setZoom] = useState(PDF_DEFAULT_ZOOM);
    const [viewerWidth, setViewerWidth] = useState(0);
    const [pageAspects, setPageAspects] = useState<Record<number, number>>({});
    const [panning, setPanning] = useState(false);
    const canvasRefs = useRef<Record<number, HTMLCanvasElement | null>>({});
    const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
    const viewportRef = useRef<HTMLDivElement>(null);
    const panState = useRef<{
        pointerId: number;
        startX: number;
        startY: number;
        scrollLeft: number;
        scrollTop: number;
    } | null>(null);
    const displayPages = useMemo(() => {/*..............................................................................*/
        return pick(Boolean(currentPage && currentPage >= 1), () => [currentPage], () => studyPages);
    }, [currentPage, studyPages]);
    const zoomMin = PDF_ZOOM_PRESETS[0];
    const zoomMax = PDF_ZOOM_PRESETS[PDF_ZOOM_PRESETS.length - 1];
    const canPan = zoom > 1.01;
    useEffect(() => {
        pick(Boolean(!open), () => {
            // eslint-disable-next-line react-hooks/set-state-in-effect -- reset zoom/width when the panel closes
            setZoom(PDF_DEFAULT_ZOOM);
            setViewerWidth(0);
        }, () => {
        });
    }, [open]);
    useEffect(() => {
        const el = viewportRef.current;
        return pick(Boolean(!el || !open), () => {
            return;
        }, () => {
            const update = () => {
                const w = el.clientWidth;
                pick(Boolean(w > 0), () => {
                    setViewerWidth(w);
                }, () => {
                });
            };
            update();
            const raf = requestAnimationFrame(update);
            const ro = new ResizeObserver(() => update());
            ro.observe(el);
            return () => {
                cancelAnimationFrame(raf);
                ro.disconnect();
            };
        });
    }, [open]);
    useEffect(() => {
        return pick(Boolean(!pdfDoc || !open), () => {
            return;
        }, () => {
            let cancelled = false;
            void (async () => {
                const __z1 = { hit: false, val: undefined as any };
                const aspects: Record<number, number> = {};
                for (const p of displayPages) {
                    await pick(Boolean(!__z1.hit), async () => {
                        await pick(Boolean(cancelled), async () => {
                            __z1.hit = true;
                        }, async () => {
                            aspects[p] = await pdfPageAspectRatio(pdfDoc, p);
                        });
                    }, async () => {
                    });
                }
                pick(Boolean(!__z1.hit), () => {
                    pick(Boolean(!cancelled), () => {
                        setPageAspects(aspects);
                    }, () => {
                    });
                }, () => {
                });
            })();
            return () => {
                cancelled = true;
            };
        });
    }, [pdfDoc, displayPages, open]);
    const pageDisplayWidth = pick(Boolean(viewerWidth > 0), () => Math.round(viewerWidth * zoom), () => 0);
    useEffect(() => {
        return pick(Boolean(!pdfDoc || !isPdf || !open || pageDisplayWidth < 1), () => {
            return;
        }, () => {
            let cancelled = false;
            // Snapshot the (stable) canvas registry so the cleanup cancels exactly the
            // renders this effect kicked off, without reading the ref at teardown time.
            const canvases = canvasRefs.current;
            void (async () => {
                const __z3 = { hit: false, val: undefined as any };
                await new Promise<void>((resolve) => {
                    requestAnimationFrame(() => resolve());
                });
                await pick(Boolean(cancelled), async () => {
                    __z3.hit = true;
                }, async () => {
                    for (const p of displayPages) {
                        await pick(Boolean(!__z3.hit), async () => {
                            await pick(Boolean(cancelled), async () => {
                                __z3.hit = true;
                            }, async () => {
                                const canvas = canvases[p];
                                await pick(Boolean(!canvas), async () => {
                                }, async () => {
                                    try {
                                        const fitScale = await pdfPageFitScale(pdfDoc, p, viewerWidth);
                                        await pick(Boolean(cancelled), async () => {
                                            __z3.hit = true;
                                        }, async () => {
                                            await renderPdfPageToCanvas(pdfDoc, p, canvas, fitScale * zoom, pageDisplayWidth);
                                        });
                                    }
                                    catch {
                                        pick(Boolean(cancelled), () => {
                                            __z3.hit = true;
                                        }, () => {
                                        });
                                    }
                                });
                            });
                        }, async () => {
                        });
                    }
                    pick(Boolean(!__z3.hit), () => {
                        pick(Boolean(!cancelled), () => {
                            const el = viewportRef.current;
                            pick(Boolean(el), () => {
                                clampPdfScroll(el);
                            }, () => {
                            });
                        }, () => {
                        });
                    }, () => {
                    });
                });
            })();
            return () => {
                cancelled = true;
                cancelAllPdfRenders(Object.values(canvases));
            };
        });
    }, [pdfDoc, displayPages, isPdf, open, viewerWidth, zoom, pageDisplayWidth]);
    useEffect(() => {
        const el = viewportRef.current;
        return pick(Boolean(!el), () => {
            return;
        }, () => {
            clampPdfScroll(el);
        });
    }, [pageDisplayWidth, displayPages, pageAspects]);
    useEffect(() => {
        return pick(Boolean(!open), () => {
            return;
        }, () => {
            const el = viewportRef.current;
            return pick(Boolean(!el), () => {
                return;
            }, () => {
                el.scrollLeft = 0;
                el.scrollTop = 0;
            });
        });
    }, [open, currentPage, displayPages]);
    function zoomIn() {
        setZoom((z) => snapPdfZoom(z, 1));
    }
    function zoomOut() {
        setZoom((z) => snapPdfZoom(z, -1));
    }
    function fitWidth() {
        setZoom(1);
        const el = viewportRef.current;
        pick(Boolean(el), () => {
            el.scrollLeft = 0;
            el.scrollTop = 0;
        }, () => {
        });
    }
    function onWheelZoom(e: WheelEvent<HTMLDivElement>) {/*..............................................................................*/
        return pick(Boolean(!e.ctrlKey && !e.metaKey), () => {
            return;
        }, () => {
            e.preventDefault();
            setZoom((z) => snapPdfZoom(z, choose(Boolean(e.deltaY > 0), -1, 1)));
        });
    }
    function onViewportScroll() {
        const el = viewportRef.current;
        return pick(Boolean(!el || panState.current), () => {
            return;
        }, () => {
            clampPdfScroll(el);
        });
    }
    function onPanPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
        return pick(Boolean(!canPan || e.button !== 0), () => {
            return;
        }, () => {
            const target = e.target as HTMLElement;
            return pick(Boolean(!target.closest("[data-pdf-page]")), () => {
                return;
            }, () => {
                const el = viewportRef.current;
                return pick(Boolean(!el), () => {
                    return;
                }, () => {
                    e.preventDefault();
                    panState.current = {
                        pointerId: e.pointerId,
                        startX: e.clientX,
                        startY: e.clientY,
                        scrollLeft: el.scrollLeft,
                        scrollTop: el.scrollTop,
                    };
                    setPanning(true);
                    el.setPointerCapture(e.pointerId);
                });
            });
        });
    }
    function onPanPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
        const pan = panState.current;
        const el = viewportRef.current;
        return pick(Boolean(!pan || pan.pointerId !== e.pointerId || !el), () => {
            return;
        }, () => {
            const dx = e.clientX - pan.startX;
            const dy = e.clientY - pan.startY;
            el.scrollLeft = pan.scrollLeft - dx;
            el.scrollTop = pan.scrollTop - dy;
            clampPdfScroll(el);
        });
    }
    function endPan(e: ReactPointerEvent<HTMLDivElement>) {
        const pan = panState.current;
        return pick(Boolean(!pan || pan.pointerId !== e.pointerId), () => {
            return;
        }, () => {
            panState.current = null;
            setPanning(false);
            const el = viewportRef.current;
            pick(Boolean(el?.hasPointerCapture(e.pointerId)), () => {
                el.releasePointerCapture(e.pointerId);
            }, () => {
            });
            pick(Boolean(el), () => {
                clampPdfScroll(el);
            }, () => {
            });
        });
    }
    return pick(Boolean(!isPdf), () => {
        const texts = pageTexts ?? {};
        const hasAnyText = displayPages.some((p) => (texts[p] || "").trim());
        return (<ScrollArea flex={1} offsetScrollbars type="auto" h="100%">
        <Stack gap="md" px="md" pt="sm" pb="xl">
          <SourceStage filename={filename} pageRange={pageRange} compact inDrawer/>
          {/*..............................................................................*/pick(Boolean(pageTextsLoading && !hasAnyText), () => (<Center py="xl">
              <Loader size="sm" color="lavender"/>
            </Center>), () => pick(Boolean(hasAnyText), () => (<Stack gap="xl">
              {displayPages.map((p) => {
                    const text = (texts[p] || "").trim();
                    return pick(Boolean(!text), () => null, () => (<Stack key={p} gap="sm">
                    {pick(Boolean((displayPages.length > 1 || studyPages.length > 1)), () => (<Text size="xs" tt="uppercase" fw={700} c="dimmed" style={{ letterSpacing: "0.06em" }}>
                        Page {p}
                      </Text>), () => (displayPages.length > 1 || studyPages.length > 1))}
                    {text.split(/\n{2,}/).map((para, j) => pick(Boolean(para.trim()), () => (<Text key={j} fz="sm" lh={1.65} c="var(--mantine-color-text)" style={{
                                fontFamily: "var(--font-serif), Georgia, serif",
                                whiteSpace: "pre-wrap",
                            }}>
                          {para.trim()}
                        </Text>), () => null))}
                  </Stack>));
                })}
            </Stack>), () => (<Text c="dimmed" size="sm" ta="center" py="lg">
              {choose(Boolean(artifactId), "Couldn’t load text for this source yet.", "No text preview for this source.")}
            </Text>)))}
        </Stack>
      </ScrollArea>);
    }, () => pick(Boolean(pdfLoading), () => (<Center flex={1}>
        <Stack align="center" gap="sm">
          <Loader size="sm" color="lavender"/>
          <Text size="sm" c="dimmed">
            Loading document…
          </Text>
        </Stack>
      </Center>), () => pick(Boolean(pdfError), () => (<Center flex={1} px="lg">
        <Stack align="center" gap="sm" maw={300}>
          <ThemeIcon size={44} radius="xl" variant="light" color="terracotta">
            <IconFileText size={22} stroke={1.6}/>
          </ThemeIcon>
          <Text c="terracotta.8" size="sm" ta="center" lh={1.55}>
            {pdfError}
          </Text>
        </Stack>
      </Center>), () => {
        const pageSurface = choose(Boolean(isDark), "white", "white");
        const pageGap = choose(Boolean(displayPages.length > 1), 8, 0);
        const activePage = displayPages[0];
        return (<Box pos="relative" h="100%" mih={0} style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <Box ref={viewportRef} flex={1} mih={0} onWheel={onWheelZoom} onScroll={onViewportScroll} onPointerDown={onPanPointerDown} onPointerMove={onPanPointerMove} onPointerUp={endPan} onPointerCancel={endPan} style={{
                overflow: "auto",
                overscrollBehavior: "contain",
                background: choose(Boolean(isDark), "var(--mantine-color-dark-8)", "var(--mantine-color-gray-1)"),
                cursor: choose(Boolean(canPan), (choose(Boolean(panning), "grabbing", "grab")), "default"),
                touchAction: choose(Boolean(panning), "none", "auto"),
            }}>
        <Box style={{
                width: "max-content",
                minWidth: "100%",
                margin: "0 auto",
                padding: "18px 16px 68px",
            }}>
          <Stack gap={choose(Boolean(pageGap > 0), 14, 0)} align="center">
            {pick(Boolean(pdfDoc), () => displayPages.map((p) => {
                const aspect = pageAspects[p] ?? 0;
                const displayHeight = pick(Boolean(pageDisplayWidth > 0 && aspect > 0), () => pdfDisplayHeight(pageDisplayWidth, aspect), () => undefined);
                const isActivePage = p === activePage;
                return (<Box key={p} data-pdf-page ref={(el) => {
                        pageRefs.current[p] = el;
                    }} pos="relative" style={{
                        width: choose(Boolean(pageDisplayWidth > 0), pageDisplayWidth, "100%"),
                        maxWidth: "100%",
                        lineHeight: 0,
                        borderRadius: 10,
                        background: pageSurface,
                        border: "1px solid var(--mantine-color-default-border)",
                        boxShadow: choose(Boolean(isActivePage), "0 0 0 2px var(--mantine-color-lavender-5), 0 14px 40px rgba(35,34,32,0.14)", "0 2px 6px rgba(35,34,32,0.06), 0 14px 30px rgba(35,34,32,0.06)"),
                        overflow: "hidden",
                        transition: "box-shadow 220ms ease",
                    }}>
                    <canvas ref={(el) => {
                        canvasRefs.current[p] = el;
                    }} style={{
                        width: choose(Boolean(pageDisplayWidth > 0), pageDisplayWidth, "100%"),
                        height: choose(Boolean(displayHeight), `${displayHeight}px`, "auto"),
                        display: "block",
                        maxWidth: "none",
                        verticalAlign: "top",
                    }}/>
                    {pick(Boolean(displayPages.length > 1), () => (<Box style={{
                            position: "absolute",
                            top: 8,
                            left: 8,
                            padding: "3px 9px",
                            borderRadius: 999,
                            fontSize: 10,
                            fontWeight: 700,
                            lineHeight: 1,
                            fontFamily: "var(--font-sans), sans-serif",
                            color: "#FFFFFF",
                            // This badge overlays the PDF canvas, which is always
                            // white in BOTH color schemes (pageSurface = "white").
                            // The lavender scale is inverted for dark mode (high
                            // shades go pale for text-on-ink), so no single
                            // lavender token stays dark in both schemes. Pin a
                            // fixed deep lavender so white label text stays legible
                            // on the invariant white page.
                            background: choose(Boolean(isActivePage), "#644791", "rgba(35,34,32,0.55)"),
                            backdropFilter: "blur(4px)",
                            WebkitBackdropFilter: "blur(4px)",
                        }}>
                        Page {p}
                      </Box>), () => displayPages.length > 1)}
                  </Box>);
            }), () => pdfDoc)}
          </Stack>
        </Box>
      </Box>

      <PdfReaderToolbar zoom={zoom} zoomMin={zoomMin} zoomMax={zoomMax} onZoomIn={zoomIn} onZoomOut={zoomOut} onFitWidth={fitWidth} isDark={isDark} canPan={canPan}/>
    </Box>);
    })));
}
function PdfReaderToolbar({ zoom, zoomMin, zoomMax, onZoomIn, onZoomOut, onFitWidth, }: {
    zoom: number;
    zoomMin: number;
    zoomMax: number;
    onZoomIn: () => void;
    onZoomOut: () => void;
    onFitWidth: () => void;
    isDark?: boolean;
    canPan?: boolean;
}) {
    const atFit = Math.abs(zoom - 1) < 0.01;
    return (<Box style={{
            position: "absolute",
            bottom: 14,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 6,
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 10px",
            borderRadius: 999,
            width: "max-content",
            maxWidth: "calc(100% - 28px)",
            flexShrink: 0,
            whiteSpace: "nowrap",
            background: "color-mix(in srgb, var(--mantine-color-body) 86%, transparent)",
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            border: "1px solid var(--mantine-color-default-border)",
            boxShadow: "0 6px 24px rgba(35,34,32,0.16)",
        }}>
      <Tooltip label="Fit page width" withArrow>
        <Button variant={choose(Boolean(atFit), "light", "subtle")} color={choose(Boolean(atFit), "lavender", "gray")} size="compact-xs" radius="xl" leftSection={<IconArrowsMaximize size={13}/>} onClick={onFitWidth} px="sm" style={{ flexShrink: 0 }} styles={{ label: { overflow: "visible", textOverflow: "clip" } }}>
          Fit width
        </Button>
      </Tooltip>
      <Box style={{
            width: 1,
            height: 18,
            flexShrink: 0,
            background: "var(--mantine-color-default-border)",
        }}/>
      <Group gap={2} wrap="nowrap" align="center" style={{ flexShrink: 0 }}>
        <Tooltip label="Zoom out" withArrow>
          <ActionIcon variant="subtle" color="gray" size="sm" radius="xl" onClick={onZoomOut} disabled={zoom <= zoomMin} aria-label="Zoom out" style={{ flexShrink: 0 }}>
            <IconZoomOut size={15} stroke={2}/>
          </ActionIcon>
        </Tooltip>
        <Text size="11px" fw={700} miw={44} ta="center" ff="monospace" style={{ flexShrink: 0 }}>
          {Math.round(zoom * 100)}%
        </Text>
        <Tooltip label="Zoom in" withArrow>
          <ActionIcon variant="subtle" color="gray" size="sm" radius="xl" onClick={onZoomIn} disabled={zoom >= zoomMax} aria-label="Zoom in" style={{ flexShrink: 0 }}>
            <IconZoomIn size={15} stroke={2}/>
          </ActionIcon>
        </Tooltip>
      </Group>
    </Box>);
}
function SourceStage({ filename, pageRange, compact = false, inDrawer = false, }: {
    filename?: string;
    pageRange?: {
        from: number;
        to: number;
    };
    compact?: boolean;
    inDrawer?: boolean;
}) {
    const shortName = filename?.replace(/\.[^.]+$/, "") ?? "Your source";
    return (<Center h={choose(Boolean(compact || inDrawer), "auto", "100%")} px="xl" py={choose(Boolean(compact || inDrawer), "xl", 0)}>
      <Stack align="center" gap="lg" maw={420}>
        <ThemeIcon size={choose(Boolean(compact || inDrawer), 52, 72)} radius="xl" variant="light" color="lavender">
          <IconFileText size={choose(Boolean(compact || inDrawer), 26, 36)} stroke={1.5}/>
        </ThemeIcon>
        <Stack gap={6} align="center">
          <Title order={choose(Boolean(compact || inDrawer), 4, 3)} ta="center" fw={600}>
            {shortName}
          </Title>
          {pick(Boolean(pageRange), () => (<Text size="sm" c="dimmed" ta="center">
              Pages {pageRange.from}-{pageRange.to}
            </Text>), () => pageRange)}
          <Text size="sm" c="dimmed" ta="center" lh={1.6}>
            Reference material for this question set. The question always comes first.
          </Text>
        </Stack>
      </Stack>
    </Center>);
}
