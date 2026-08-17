// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { use, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, Box, Button, Center, Drawer, Group, Paper, Progress, SegmentedControl, Skeleton, Stack, Text, Title, } from "@mantine/core";
import { useDisclosure, useLocalStorage, useMediaQuery } from "@mantine/hooks";
import { IconArrowLeft, IconFileText, IconMessageCircle, IconNotebook } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { apiFetchBytes, apiGet, apiPost, apiPostSSE, apiUrl, ensureGuestSession, isArtifactId } from "@/lib/api/client";
import { notifications } from "@mantine/notifications";
import { queryKeys, useArtifactPagesQuery, useArtifactQuery, useAssertionQuery, useBrainstormActions, useSavedNotesQuery, useSavedNotesActions, useStudyReportQuery, } from "@/lib/api/queries";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";
import { BrainstormView } from "@/app/workspace/_components/BrainstormView";
import { ExplainView } from "@/app/workspace/_components/ExplainView";
import { NotesView } from "@/app/workspace/_components/NotesView";
import { FlashcardsView } from "@/app/workspace/_components/FlashcardsView";
import { MemoryPalaceView } from "@/app/workspace/_components/MemoryPalaceView";
import { QuizBuilderView } from "@/app/workspace/_components/QuizBuilderView";
import { InterviewView } from "@/app/workspace/_components/InterviewView";
import { MainsView } from "@/app/workspace/_components/MainsView";
import { CodingView } from "@/app/workspace/_components/CodingView";
import { ResumeView } from "@/app/workspace/_components/ResumeView";
import { ProgressView } from "@/app/workspace/_components/ProgressView";
import { PdfReader } from "@/app/workspace/_components/PdfReader";
import { TutorPanel, READ_CHAT_SUGGESTIONS, BRAINSTORM_SUGGESTIONS, } from "@/app/workspace/_components/TutorPanel";
import { useStudyNav, type StudyMode } from "@/app/workspace/_components/studyNav";
import { useTutorChat } from "@/app/workspace/_components/useTutorChat";
import { McqHeroPanel, McqReviewView } from "@/app/workspace/_components/McqPanels";
import { MCQ_CONTENT_MAX } from "@/app/_components/mcq/McqCard";
import { StudySourcePanel } from "@/app/workspace/_components/StudySourcePanel";
import { StudyEdgeTrigger } from "@/app/workspace/_components/StudyRails";
import { FloatingPanel, type FloatingPanelGeometry, type FloatingPanelHandle } from "@/app/workspace/_components/FloatingPanel";
import { StudyMobileShell } from "@/app/workspace/_components/StudyMobileShell";
import { loadPackForDocument, seedAssertionCache } from "@/lib/offline/reader";
import { localGrade, nextAssertionId } from "@/lib/offline/engine";
import { enqueueGrade } from "@/lib/offline/outbox";
import { syncPack } from "@/lib/offline/sync";
import type { OfflineAssertion, OfflinePack } from "@/lib/offline/db";
import { SelectionQuote } from "@/app/workspace/_components/SelectionQuote";
import { StudyMetaBar, type StudyAlign } from "@/app/workspace/_components/StudyMetaBar";
import { LessonScreen } from "@/app/workspace/_components/LessonScreen";
import { suggestNextPageRange, TestResultsScreen, DocumentCompleteScreen, StudyRangeReselectOverlay, PageCompleteInterstitial, } from "@/app/workspace/_components/StudyScreens";
import { PageSelectionScreen, buildPageSliderMarks } from "@/app/workspace/_components/PageSelectionScreen";
import { STUDY_DESKTOP_BP, STUDY_COMPACT_BP, pagesInRange, computeFloatingLaneInsets, type AnsweredCard, } from "@/app/workspace/_components/studyLayout";
import { PetPlayground } from "@/app/_components/pets/PetPlayground";
import { indexingStage } from "@/lib/constants";
import { isBackgroundPrepActive, prepProgressPercent, prepScreenStatus, type PrepProgress, } from "@/lib/prepStatus";
import { getCachedPdfDocument, loadPdfForArtifact } from "@/lib/pdf";
import { budgetModeQuery, defaultWorkspaceMode } from "@/lib/studyPreferences";
import { formatMcqStemForDisplay } from "@/lib/mcqStemFormat";
import { normalizeMcqOptions, type ArtifactMeta, type AssertionPayload, type McqState, type PagesInfo, } from "@/lib/types";
import { useIsDark } from "@/lib/useIsDark";
import { useSearchParam } from "@/lib/useSearchParam";
type LearnAnsweredItem = {
    assertion_id: string;
    stem: string;
    options: string[];
    selected_index: number;
    selected_indices?: number[];
    correct: boolean;
    correct_index: number;
    correct_indices?: number[];
    feedback: string | null;
    concept?: string | null;
    first_try_correct: boolean;
};
function learnAnsweredToCards(items: LearnAnsweredItem[]): AnsweredCard[] {
    return items.map((item) => ({
        assertionId: item.assertion_id,
        stem: item.stem,
        options: item.options,
        selectedIndex: item.selected_index,
        selectedIndices: item.selected_indices,
        gradeState: {
            correct: item.correct,
            correctIndex: item.correct_index,
            correctIndices: item.correct_indices,
        },
        feedback: item.feedback,
        concept: item.concept ?? null,
        firstTryCorrect: item.first_try_correct,
    }));
}
/** Stale learn-queue ticks can arrive after Continue and rewind current_assertion_id. */
function coalesceQueueAssertionId(incoming: string | null | undefined, prevCurrent: string | null | undefined, answeredIds: ReadonlySet<string>): string | null | undefined {
    return pick(Boolean(!incoming), () => incoming, () => pick(Boolean(!prevCurrent || incoming === prevCurrent), () => incoming, () => pick(Boolean(answeredIds.has(incoming)), () => prevCurrent, () => incoming)));
}
function mergeQueueState(data: McqState, prev: McqState | null, answeredIds: ReadonlySet<string>): McqState {
    const resolved = coalesceQueueAssertionId(data.current_assertion_id, prev?.current_assertion_id ?? null, answeredIds);
    return pick(Boolean(resolved === data.current_assertion_id), () => data, () => ({ ...data, current_assertion_id: resolved ?? null }));
}
export default function WorkspaceArtifactPage({ params, }: {
    params: Promise<{
        artifactId: string;
    }>;
}) {
    // `use(params)` first so a suspend does not leave later hooks half-mounted.
    const { artifactId } = use(params);
    const router = useRouter();
    const queryClient = useQueryClient();
    const invalidArtifactId = !isArtifactId(artifactId);
    // Avoid next/navigation useSearchParams: Next 16 DEV conditionally calls use()
    // and trips Rules of Hooks in this page (useContext vs useState at position 5).
    const urlMode = useSearchParam("mode");
    const isLg = useMediaQuery(STUDY_DESKTOP_BP, false, { getInitialValueInEffect: true });
    const isCompact = useMediaQuery(STUDY_COMPACT_BP, false, { getInitialValueInEffect: true });
    // Phone OR tablet study shell (<992): denser chrome. True phone (<768) also gets
    // the mode dropdown (tablet keeps the desktop sidebar mode list).
    const isNarrow = !isLg;
    const isDark = useIsDark();
    // Mode selection lives in the workspace layout so the global left sidebar can
    // host the mode navigator (below the source list) instead of a second rail.
    const { mode, setMode, setActive: setStudyNavActive } = useStudyNav();
    useEffect(() => {
        setStudyNavActive(true);
        return () => setStudyNavActive(false);
    }, [setStudyNavActive]);
    const [artifact, setArtifact] = useState<ArtifactMeta | null>(null);
    const [pages, setPages] = useState<PagesInfo | null>(null);
    const [setupError, setSetupError] = useState<string | null>(null);
    const [confirming, setConfirming] = useState(false);
    const [confirmingMode, setConfirmingMode] = useState<"now" | "background" | null>(null);
    const [selectedPages, setSelectedPages] = useState<number[]>([]);
    const [lastClickedPage, setLastClickedPage] = useState<number | null>(null);
    const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null);
    const [pdfError, setPdfError] = useState<string | null>(null);
    const [pdfLoading, setPdfLoading] = useState(false);
    const [pageTexts, setPageTexts] = useState<Record<number, string>>({});
    const [pageTextsLoading, setPageTextsLoading] = useState(false);
    const thumbCanvasRefs = useRef<Record<number, HTMLCanvasElement | null>>({});
    const [queue, setQueue] = useState<McqState | null>(null);
    const [question, setQuestion] = useState("Loading questions…");
    const [options, setOptions] = useState<string[]>([]);
    const [selected, setSelected] = useState<string | null>(null);
    // Multi-select ("select all that apply") state: whether the current item is multi,
    // and which option indices are currently ticked.
    const [isMulti, setIsMulti] = useState(false);
    const [multiSelected, setMultiSelected] = useState<number[]>([]);
    // The concept the current question tests - captured per question for the report card.
    const [currentConcept, setCurrentConcept] = useState<string | null>(null);
    const [feedback, setFeedback] = useState<string | null>(null);
    const [gradeState, setGradeState] = useState<{
        correct: boolean;
        correctIndex: number;
        correctIndices?: number[];
    } | null>(null);
    // Calibration signal: learner's stated confidence (0=guess, 1=unsure, 2=confident)
    // before reveal. Sent with the grade; reset when the next question loads.
    const [confidence, setConfidence] = useState<number | null>(null);
    // Pin the graded card until Continue — SSE learn-queue advances current_assertion_id
    // as soon as the answer is recorded, which would otherwise wipe feedback mid-coach.
    const [pinnedAssertionId, setPinnedAssertionId] = useState<string | null>(null);
    const pinnedAssertionIdRef = useRef<string | null>(null);
    // Prefetched next id from grade/stream "next" event — Continue swaps without a full queue RT.
    const pendingNextAssertionIdRef = useRef<string | null>(null);
    // Graded assertion ids — blocks SSE/poll from rewinding to an already-answered card.
    const answeredAssertionIdsRef = useRef<Set<string>>(new Set());
    const advancingMcqRef = useRef(false);
    const [submitting, setSubmitting] = useState(false);
    const [mcqLoading, setMcqLoading] = useState(true);
    // Answered-question history + a "review" cursor (null = on the live question).
    const [answeredHistory, setAnsweredHistory] = useState<AnsweredCard[]>([]);
    const [reviewIndex, setReviewIndex] = useState<number | null>(null);
    const [flagBusy, setFlagBusy] = useState(false);
    const [flaggedIds, setFlaggedIds] = useState<Record<string, true>>({});
    // Offline Mode (ADR 0006): when a pack is downloaded for this artifact, the
    // queue + grading branches read from Dexie instead of the network. onlineDeck
    // holds the pack's assertions; offlinePackIds is the answered set (seeded
    // from mastery + appended per grade) used to pick the next card locally.
    const [offlineDeck, setOfflineDeck] = useState<OfflineAssertion[] | null>(null);
    const [offlinePack, setOfflinePack] = useState<OfflinePack | null>(null);
    const [offlineAnsweredIds, setOfflineAnsweredIds] = useState<Set<string>>(new Set());
    const studyingOffline = offlineDeck !== null;
    const savedNotesQuery = useSavedNotesQuery(artifactId);
    const savedNotesActions = useSavedNotesActions(artifactId);
    const [readerNotesOpen, setReaderNotesOpen] = useState(false);
    const [readerMobileTab, setReaderMobileTab] = useState<"reader" | "buddy">("reader");
    const [sourceOpen, { open: openSource, close: closeSource }] = useDisclosure(false);
    const [tutorOpen, { open: openTutor, close: closeTutor }] = useDisclosure(false);
    // The floating Source/Tutor windows own their own geometry (see FloatingPanel),
    // so the workspace only needs the row ref to bound them.
    const studyRowRef = useRef<HTMLDivElement>(null);
    const tutorPanelRef = useRef<FloatingPanelHandle>(null);
    const sourcePanelRef = useRef<FloatingPanelHandle>(null);
    const [tutorGeometry, setTutorGeometry] = useState<FloatingPanelGeometry | null>(null);
    const [sourceGeometry, setSourceGeometry] = useState<FloatingPanelGeometry | null>(null);
    const [studyRowWidth, setStudyRowWidth] = useState(0);
    const [reselectOpen, setReselectOpen] = useState(false);
    // Learn lessons the learner has dismissed with "Start the questions" (one per
    // page, persisted per document). Returning mid-MCQ must not replay the lesson —
    // that is inferred from queue progress on the current page.
    const [lessonDismissedPages, setLessonDismissedPages] = useLocalStorage<number[]>({
        key: `zv-lesson-dismissed:${artifactId}`,
        defaultValue: [],
        getInitialValueInEffect: false,
    });
    const lessonDismissed = useMemo(() => new Set(lessonDismissedPages), [lessonDismissedPages]);
    const [lessonForcedOpenPage, setLessonForcedOpenPage] = useState<number | null>(null);
    // Bumped when the learner quotes selected MCQ text into chat, so the mobile
    // shell can jump to the tutor tab (desktop just opens the floating panel).
    const [mobileTutorFocus, setMobileTutorFocus] = useState(0);
    useEffect(() => {
        pick(Boolean(mode === "test"), () => {
            closeTutor();
        }, () => {
        });
    }, [mode, closeTutor]);
    // Study-column alignment (desktop): centred, or pinned left beside the source panel.
    const [studyAlign, setStudyAlign] = useLocalStorage<StudyAlign>({
        key: "zv-study-align",
        defaultValue: "center",
        getInitialValueInEffect: false,
    });
    useEffect(() => {
        const el = studyRowRef.current;
        return pick(Boolean(!el), () => {
            return;
        }, () => {
            const measure = () => {
                const w = el.clientWidth;
                setStudyRowWidth((prev) => (choose(Boolean(Math.abs(prev - w) < 2), prev, w)));
            };
            measure();
            const ro = new ResizeObserver(measure);
            ro.observe(el);
            return () => ro.disconnect();
        });
    }, [isLg]);
    function handleStudyAlignChange(align: StudyAlign) {
        setStudyAlign(align);
        pick(Boolean(align === "left"), () => {
            pick(Boolean(tutorOpen), () => {
                tutorPanelRef.current?.snapSide("right");
            }, () => {
            });
            pick(Boolean(sourceOpen), () => {
                sourcePanelRef.current?.snapSide("left");
            }, () => {
            });
        }, () => {
            pick(Boolean(align === "right"), () => {
                pick(Boolean(tutorOpen), () => {
                    tutorPanelRef.current?.snapSide("left");
                }, () => {
                });
                pick(Boolean(sourceOpen), () => {
                    sourcePanelRef.current?.snapSide("right");
                }, () => {
                });
            }, () => {
            });
        });
    }
    const displayAssertionId = pinnedAssertionId ??
        pinnedAssertionIdRef.current ??
        queue?.current_assertion_id ??
        null;
    const assertionQuery = useAssertionQuery(displayAssertionId);
    const questionSourcePage = useMemo(() => {
        const pn = assertionQuery.data?.payload?.page_number;
        return pick(Boolean(typeof pn === "number" && pn > 0), () => pn, () => {
            const fromQueue = queue?.current_page;
            return choose(Boolean(typeof fromQueue === "number" && fromQueue > 0), fromQueue, null);
        });
    }, [assertionQuery.data?.payload, queue?.current_page]);
    // Tutor chat (per-mode conversation: hydrate, stream, regenerate, clear, plus
    // Read-mode Study-Buddy quote/ask/save) lives in its own hook.
    const { chatInput, setChatInput, chatMessages, chatBusy, chatContextReady, sendChat, clearChat, quoteToComposer, askBuddy, askBuddyWithWikipedia, saveNote, regenerateChat, editChatFromUser, stopChat, addChatMcqsToLearn, mcqPersistState, } = useTutorChat({
        artifactId,
        mode,
        enabled: !invalidArtifactId,
        queue,
        currentAssertionId: displayAssertionId,
        questionPage: questionSourcePage,
        selected,
        multiSelected,
        isMulti,
        gradeState,
        savedNotesActions,
        onSavedNote: () => setReaderNotesOpen(true),
    });
    // Same page component instance survives sidebar hops A → B — wipe mirrored local
    // state so B never flashes A's filename, queue, or answer history. Also reset mode
    // so a Coding/Resume session on A doesn't open B in the wrong surface.
    useEffect(() => {
        /* eslint-disable react-hooks/set-state-in-effect -- artifactId change must reset local study state without remounting the route */
        setArtifact(null);
        setPages(null);
        setSetupError(null);
        setConfirming(false);
        setSelectedPages([]);
        setLastClickedPage(null);
        setPdfDoc(null);
        setPdfError(null);
        setPdfLoading(false);
        thumbCanvasRefs.current = {};
        setQueue(null);
        setQuestion("Loading questions…");
        setOptions([]);
        setSelected(null);
        setIsMulti(false);
        setMultiSelected([]);
        setCurrentConcept(null);
        setFeedback(null);
        setGradeState(null);
        pinnedAssertionIdRef.current = null;
        setPinnedAssertionId(null);
        pendingNextAssertionIdRef.current = null;
        answeredAssertionIdsRef.current = new Set();
        setSubmitting(false);
        setMcqLoading(true);
        setAnsweredHistory([]);
        setReviewIndex(null);
        // Offline Mode: clear the packed-deck projection so the next artifact loads fresh.
        setOfflineDeck(null);
        setOfflinePack(null);
        setOfflineAnsweredIds(new Set());
        setLessonForcedOpenPage(null);
        // Non-newspaper: Settings Relaxed/Exam. Newspaper default Learn unless ?mode=test
        // (docs/QUESTION_BUDGET_ENGINE.md §0 — no Learn/Test modal on open).
        setMode(pick(Boolean(urlMode === "test"), () => "test", () => defaultWorkspaceMode()));
    }, [artifactId, setMode, urlMode]);
    // Brainstorm turns the same tutor panel into the ideation partner: the ANGLES each
    // reply ends with become chips you can explore (send as the next turn) or keep (pin
    // to the idea board). Spread into every TutorPanel so desktop and mobile match.
    const brainstormActions = useBrainstormActions(artifactId);
    const chatMcqProps = {
        onAddMcqs: addChatMcqsToLearn,
        mcqPersistBusy: mcqPersistState.busy,
        mcqPersistDone: mcqPersistState.done,
        mcqPersistError: mcqPersistState.error,
    };
    const brainstormChatProps = choose(Boolean(mode === "brainstorm"), {
        suggestions: BRAINSTORM_SUGGESTIONS,
        emptyHint: "Think out loud about this source. Every reply ends with three angles you could pull.",
        onExploreAngle: (angle: string) => askBuddy(angle),
        onKeepAngle: (angle: string) => void brainstormActions.keep(angle),
        ...chatMcqProps,
    }, { ...chatMcqProps });
    const selectedRange = useMemo(() => {
        return pick(Boolean(artifact?.meta?.selected_range), () => artifact.meta.selected_range, () => pick(Boolean(!(artifact?.ingest_kind === "newspaper" ||
            artifact?.meta?.newspaper ||
            artifact?.meta?.hide_source) ||
            !pages), () => undefined, () => ({
            from: 1,
            to: pages.page_count,
            pages: pagesInRange(1, pages.page_count),
        })));
    }, [artifact?.meta?.selected_range, artifact?.ingest_kind, artifact?.meta?.newspaper, artifact?.meta?.hide_source, pages]);
    const studyRangeKey = pick(Boolean(selectedRange), () => `${selectedRange.from}:${selectedRange.to}:${(selectedRange.pages ?? []).join(",")}`, () => "");
    const apiPageCount = pages?.page_count ?? artifact?.meta?.page_count ?? 1;
    const pageCount = Math.max(apiPageCount, pdfDoc?.numPages ?? 0, 1);
    const sortedSelection = useMemo(() => [...selectedPages].filter((p) => pick(Boolean(p >= 1), () => p <= pageCount, () => p >= 1)).sort((a, b) => a - b), [selectedPages, pageCount]);
    const sliderFrom = sortedSelection[0] ?? 1;
    const sliderTo = choose(Boolean(sortedSelection.length > 0), sortedSelection[sortedSelection.length - 1], 1);
    const sliderMarks = useMemo(() => buildPageSliderMarks(pageCount), [pageCount]);
    const stem = choose(Boolean(displayAssertionId), choose(Boolean(mcqLoading && !question), "", question), "");
    useEffect(() => {
        pick(Boolean(invalidArtifactId), () => {
            router.replace("/workspace");
        }, () => {
        });
    }, [invalidArtifactId, router]);
    const artifactQuery = useArtifactQuery(artifactId, !invalidArtifactId);
    const isNewspaper = Boolean(artifactQuery.data?.ingest_kind === "newspaper" ||
        artifactQuery.data?.meta?.newspaper ||
        artifactQuery.data?.meta?.hide_source ||
        artifact?.ingest_kind === "newspaper" ||
        artifact?.meta?.newspaper ||
        artifact?.meta?.hide_source);
    const paperSlug = artifactQuery.data?.meta?.paper_slug ?? artifact?.meta?.paper_slug;
    const paperTitle = artifactQuery.data?.meta?.paper_title ?? artifact?.meta?.paper_title ?? null;
    const editionDate = artifactQuery.data?.meta?.edition_date ?? artifact?.meta?.edition_date ?? null;
    const newspaperBackHref = choose(Boolean(paperSlug), `/practice/newspaper/${paperSlug}`, "/practice/newspaper");
    // Newspaper: always enter Learn unless URL asked for Test. No chooser modal.
    // Sidebar Test after open is fine (this effect does not depend on mode).
    useEffect(() => {
        return pick(Boolean(!isNewspaper), () => {
            return;
        }, () => {
            setMode(choose(Boolean(urlMode === "test"), "test", "learn"));
        });
    }, [isNewspaper, urlMode, artifactId, setMode]);
    // PDF-less editions: Read is a dead end — bounce to Learn, never a Learn/Test popup.
    useEffect(() => {/*..............................................................................*/
        pick(Boolean(isNewspaper && mode === "read"), () => {
            setMode("learn");
        }, () => {
        });
    }, [isNewspaper, mode, setMode]);
    const pagesQuery = useArtifactPagesQuery(artifactId, pick(Boolean(!invalidArtifactId), () => pick(Boolean(artifactQuery.data), () => !isNewspaper, () => Boolean(artifactQuery.data)), () => !invalidArtifactId));
    // Persistent report card - only fetched once a range is complete.
    const studyReportQuery = useStudyReportQuery(artifactId, pick(Boolean(queue?.document_complete), () => !invalidArtifactId, () => Boolean(queue?.document_complete)));
    useEffect(() => {
        pick(Boolean(artifactQuery.data), () => {
            setArtifact(artifactQuery.data);
        }, () => {
        });
        pick(Boolean(artifactQuery.error), () => {
            setSetupError(choose(Boolean(artifactQuery.error instanceof Error), artifactQuery.error.message, "Could not load source"));
        }, () => {
        });
    }, [artifactQuery.data, artifactQuery.error]);
    useEffect(() => {
        return pick(Boolean(!pagesQuery.data), () => {
            return;
        }, () => {
            const data = pagesQuery.data;
            const count = Math.max(data.page_count ?? 1, 1);
            // eslint-disable-next-line react-hooks/set-state-in-effect -- sync server pages; default/clamp selection (artifactId reset already clears)
            setPages(data);
            return pick(Boolean(artifact?.meta?.selected_range), () => {
                return;
            }, () => {
                setSelectedPages((prev) => {
                    const clamped = prev.filter((p) => pick(Boolean(p >= 1), () => p <= count, () => p >= 1));
                    return pick(Boolean(clamped.length > 0), () => clamped, () => pick(Boolean(count === 1), () => [1], () => []));
                });
                setLastClickedPage((prev) => {/*..............................................................................*/
                    return pick(Boolean(prev !== null && prev >= 1 && prev <= count), () => prev, () => choose(Boolean(count === 1), 1, null));
                });
            });
        });
    }, [pagesQuery.data, artifact?.meta?.selected_range]);
    // Newspaper: no PDF/pages endpoint for learners — synthesize PagesInfo from meta.
    useEffect(() => {
        return pick(Boolean(!isNewspaper || !artifactQuery.data), () => {
            return;
        }, () => {
            const meta = artifactQuery.data.meta ?? {};
            const range = meta.selected_range;
            const count = Math.max(meta.page_count ?? range?.to ?? range?.pages?.length ?? 1, 1);
            // eslint-disable-next-line react-hooks/set-state-in-effect -- newspaper has no /pages route; seed from artifact meta
            setPages({ page_count: count, status: artifactQuery.data.status });
            pick(Boolean(range?.pages?.length), () => {
                setSelectedPages(range.pages);
            }, () => {/*..............................................................................*/
                pick(Boolean(range?.from && range?.to), () => {
                    setSelectedPages(pagesInRange(range.from, range.to));
                }, () => {
                    setSelectedPages(pagesInRange(1, count));
                });
            });
        });
    }, [isNewspaper, artifactQuery.data]);
    // Explicit indexing → ready poll. A freshly-uploaded PDF first settles the
    // artifact query on status "pending" (awaiting page selection); React Query
    // does not reliably (re)arm a refetchInterval that was previously false once
    // the status transitions to "indexing" after the learner confirms pages - so
    // the full-screen "indexing N%" loader would freeze at its last sampled value
    // until a manual refresh. Own the poll here so it always runs while indexing
    // and stops the instant the document is ready.
    useEffect(() => {
        const prepping = artifact?.status === "indexing" ||
            artifact?.status === "prepping" ||
            isBackgroundPrepActive(artifact?.meta);
        return pick(Boolean(invalidArtifactId || !prepping), () => {
            return;
        }, () => {
            let cancelled = false;
            const poll = async () => {
                const __z1 = { hit: false, val: undefined as any };
                try {
                    const data = await apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`);
                    pick(Boolean(cancelled), () => {
                        __z1.hit = true;
                    }, () => {
                        setArtifact(data);
                        queryClient.setQueryData(queryKeys.artifact(artifactId), data);
                    });
                }
                catch {
                }
            };
            const id = window.setInterval(() => void poll(), 2500);
            return () => {
                cancelled = true;
                window.clearInterval(id);
            };
        });
    }, [invalidArtifactId, artifactId, artifact?.status, artifact?.meta?.prep_mode, artifact?.meta?.prep_complete, queryClient]);
    useEffect(() => {
        void ensureGuestSession();
    }, []);
    const queueRef = useRef(queue);
    queueRef.current = queue;
    const isPdf = artifact?.content_type === "application/pdf";
    useEffect(() => {
        return pick(Boolean(invalidArtifactId || !artifact || !isPdf || isNewspaper), () => {
            return;
        }, () => {
            // eslint-disable-next-line react-hooks/set-state-in-effect -- async PDF document load - inherently an effect
            setPdfError(null);
            const cached = getCachedPdfDocument(artifactId);
            return pick(Boolean(cached), () => {
                setPdfDoc(cached);
                setPdfLoading(false);
                return;
            }, () => {
                let cancelled = false;
                setPdfDoc(null);
                setPdfLoading(true);
                void (async () => {
                    try {
                        const pdf = await loadPdfForArtifact(artifactId, {
                            url: `/api/documents/${artifactId}/file`,
                            fetchBytes: () => apiFetchBytes(`/api/documents/${artifactId}/file`),
                        });
                        pick(Boolean(!cancelled), () => {
                            setPdfDoc(pdf);
                        }, () => {
                        });
                    }
                    catch (e) {
                        pick(Boolean(!cancelled), () => {
                            setPdfError(choose(Boolean(e instanceof Error), e.message, "Could not load PDF"));
                        }, () => {
                        });
                    }
                    finally {
                        pick(Boolean(!cancelled), () => {
                            setPdfLoading(false);
                        }, () => {
                        });
                    }
                })();
                return () => {
                    cancelled = true;
                };
            });
        });
    }, [artifactId, invalidArtifactId, isPdf, isNewspaper, artifact?.id]);
    useEffect(() => {
        return pick(Boolean(invalidArtifactId || !artifact || isPdf || isNewspaper), () => {
            // eslint-disable-next-line react-hooks/set-state-in-effect -- clear non-PDF preview state when switching sources
            setPageTexts({});
            setPageTextsLoading(false);
            return;
        }, () => {
            let cancelled = false;
            setPageTextsLoading(true);
            void apiGet<{
                pages: {
                    page: number;
                    text: string;
                }[];
            }>(`/api/documents/${artifactId}/pages`)
                .then((data) => {
                return pick(Boolean(cancelled), () => {
                    return;
                }, () => {
                    const next: Record<number, string> = {};
                    for (const item of data.pages || []) {
                        next[Number(item.page)] = String(item.text || "");
                    }
                    setPageTexts(next);
                });
            })
                .catch(() => {
                pick(Boolean(!cancelled), () => {
                    setPageTexts({});
                }, () => {
                });
            })
                .finally(() => {
                pick(Boolean(!cancelled), () => {
                    setPageTextsLoading(false);
                }, () => {
                });
            });
            return () => {
                cancelled = true;
            };
        });
    }, [artifactId, invalidArtifactId, isPdf, isNewspaper, artifact?.id]);
    useEffect(() => {
        return pick(Boolean(!pdfDoc?.numPages || pdfDoc.numPages <= 1), () => {
            return;
        }, () => {
            const n = pdfDoc.numPages;
            // eslint-disable-next-line react-hooks/set-state-in-effect -- clamp the selection when the loaded PDF's page count changes
            setSelectedPages((prev) => prev.filter((p) => p <= n));
        });
    }, [pdfDoc]);
    const studyPages = useMemo(() => {
        return pick(Boolean(!selectedRange), () => [], () => pick(Boolean(selectedRange.pages?.length), () => [...selectedRange.pages].sort((a, b) => a - b), () => {
            const pages: number[] = [];
            for (let p = selectedRange.from; p <= selectedRange.to; p += 1) {
                pages.push(p);
            }
            return pages;
        }));
    }, [selectedRange]);
    const queueStreamRef = useRef<EventSource | null>(null);
    // Tracks whether the live SSE queue stream is connected. The fallback poll
    // below runs ONLY while the stream is down - a healthy stream already pushes
    // every queue update, so polling on top of it would just double-fetch.
    const [streamConnected, setStreamConnected] = useState(false);
    const learnQueuePath = useMemo(() => {
        const path = `/api/artifacts/${artifactId}/learn-queue`;
        return pick(Boolean(mode === "learn" || mode === "test"), () => `${path}?mode=${budgetModeQuery(mode)}`, () => path);
    }, [artifactId, mode]);
    const learnQueueStreamPath = useMemo(() => {
        const path = `/api/artifacts/${artifactId}/learn-queue/stream`;
        return pick(Boolean(mode === "learn" || mode === "test"), () => `${path}?mode=${budgetModeQuery(mode)}`, () => path);
    }, [artifactId, mode]);
    useEffect(() => {
        return pick(Boolean(invalidArtifactId || !studyRangeKey || artifact?.status === "indexing"), () => {
            return;
        }, () => {
            let cancelled = false;
            // Offline Mode (ADR 0006): if a pack is downloaded for this artifact, study
            // from Dexie — no learn-queue fetch, no SSE, no poll. Seeds the assertion
            // cache so useAssertionQuery returns instantly with no network round-trip.
            // Falls through to the online path when there is no pack (or no IndexedDB).
            // Both branches await the SAME probe: reading `offlineDeck` from the render
            // closure could never see the value the other branch had just set, so a
            // downloaded pack still opened the online queue + SSE on top of it.
            const offlineProbe = pick(Boolean(typeof window !== "undefined" && "indexedDB" in window), () => loadPackForDocument(artifactId).catch(() => null), () => Promise.resolve(null));
            void (async () => {
                const loaded = await offlineProbe;
                return pick(Boolean(cancelled || !loaded), () => {
                    // No offline pack → the online loader below takes over.
                    return;
                }, () => {
                    setOfflineDeck(loaded.deck);
                    setOfflinePack(loaded.pack);
                    setOfflineAnsweredIds(new Set(loaded.pack.mastery.answered_ids));
                    seedAssertionCache(loaded.deck, queryClient, (id) => queryKeys.assertion(id));
                    const nextId = nextAssertionId(loaded.deck, new Set(loaded.pack.mastery.answered_ids));
                    setQueue({
                        current_assertion_id: nextId,
                        questions_generated: loaded.deck.length,
                        questions_answered: loaded.pack.mastery.answered_ids.length,
                        document_complete: nextId === null,
                        pool_available: loaded.deck.length,
                        study_mode: "classic",
                    } as McqState);
                    setMcqLoading(false);
                });
            })();
            void (async () => {
                const __z4 = { hit: false, val: undefined as any };
                await pick(Boolean(await offlineProbe), async () => {
                    __z4.hit = true;
                }, async () => {
                    await ensureGuestSession();
                    await pick(Boolean(cancelled), async () => {
                        __z4.hit = true;
                    }, async () => {
                        try {
                            const data = await apiGet<McqState>(learnQueuePath);
                            pick(Boolean(cancelled), () => {
                                __z4.hit = true;
                            }, () => {
                                setQueue(data);
                                setMcqLoading(false);
                            });
                        }
                        catch {
                        }
                        pick(Boolean(!__z4.hit), () => {
                            pick(Boolean(cancelled), () => {
                                __z4.hit = true;
                            }, () => {
                                queueStreamRef.current?.close();
                                const url = apiUrl(learnQueueStreamPath);
                                const es = new EventSource(url, { withCredentials: true });
                                queueStreamRef.current = es;
                                es.onopen = () => {
                                    pick(Boolean(!cancelled), () => {
                                        setStreamConnected(true);
                                    }, () => {
                                    });
                                };
                                es.addEventListener("queue", (ev) => {
                                    return pick(Boolean(cancelled), () => {
                                        return;
                                    }, () => {
                                        try {
                                            const data = JSON.parse((ev as MessageEvent).data) as McqState;
                                            setQueue((prev) => {
                                                // While graded feedback is pinned, keep showing the answered card —
                                                // still absorb pool/progress fields so Continue stays warm.
                                                const pinned = pinnedAssertionIdRef.current;
                                                return pick(Boolean(pinned && prev), () => {
                                                    // Cook often finishes the next card during feedback. Stash it so
                                                    // Continue can swap instantly; otherwise pin overwrite + a hung
                                                    // SSE leaves the learner on "Writing your next question" forever.
                                                    const realNext = data.current_assertion_id;
                                                    pick(Boolean(realNext &&
                                                        realNext !== pinned &&
                                                        !pendingNextAssertionIdRef.current), () => {
                                                        pendingNextAssertionIdRef.current = realNext;
                                                    }, () => {
                                                    });
                                                    return {
                                                        ...data,
                                                        current_assertion_id: pinned,
                                                    };
                                                }, () => mergeQueueState(data, prev, answeredAssertionIdsRef.current));
                                            });
                                            setMcqLoading(false);
                                        }
                                        catch {
                                        }
                                    });
                                });
                                es.addEventListener("done", (ev) => {
                                    return pick(Boolean(cancelled), () => {
                                        return;
                                    }, () => {
                                        try {
                                            const data = JSON.parse((ev as MessageEvent).data) as McqState;
                                            setQueue(data);
                                        }
                                        catch {
                                        }
                                        es.close();
                                        pick(Boolean(queueStreamRef.current === es), () => {
                                            queueStreamRef.current = null;
                                        }, () => {
                                        });
                                        setStreamConnected(false);
                                    });
                                });
                                es.addEventListener("error", () => {
                                    return pick(Boolean(cancelled), () => {
                                        return;
                                    }, () => {
                                        es.close();
                                        pick(Boolean(queueStreamRef.current === es), () => {
                                            queueStreamRef.current = null;
                                        }, () => {
                                        });
                                        setStreamConnected(false);
                                        void (async () => {
                                            const __z5 = { hit: false, val: undefined as any };
                                            try {
                                                const data = await apiGet<McqState>(learnQueuePath);
                                                pick(Boolean(cancelled), () => {
                                                    __z5.hit = true;
                                                }, () => {
                                                    setQueue(data);
                                                });
                                            }
                                            catch {
                                                setQuestion("Sign in or reload to load questions.");
                                            }
                                            finally {
                                                pick(Boolean(!cancelled), () => {
                                                    setMcqLoading(false);
                                                }, () => {
                                                });
                                            }
                                        })();
                                    });
                                }, { once: true });
                            });
                        }, () => {
                        });
                    });
                });
            })();
            return () => {
                cancelled = true;
                queueStreamRef.current?.close();
                queueStreamRef.current = null;
                setStreamConnected(false);
            };
        });
    }, [artifactId, invalidArtifactId, studyRangeKey, artifact?.status, learnQueuePath, learnQueueStreamPath, queryClient]);
    useEffect(() => {
        return pick(Boolean(invalidArtifactId || !studyRangeKey || artifact?.status !== "ready"), () => {
            return;
        }, () => {
            // Waiting for the next card with an empty pool: always poll. A "connected"
            // EventSource can hang without delivering (seen as permanent 92% "Writing
            // your next question" while /learn-queue already has current_assertion_id).
            // Stop once the queue is terminal — document done, nothing to quiz, or
            // reselect prompt — otherwise pool_available===0 polls forever.
            const queueSettled = Boolean(queue?.document_complete) ||
                Boolean(queue?.no_questions_reason) ||
                Boolean(queue?.prompt_reselect_pages);
            const waitingForNextCard = pick(Boolean(!queueSettled), () => pick(Boolean(!pinnedAssertionIdRef.current), () => pick(Boolean(!queue?.current_assertion_id), () => (Boolean(queue?.generation_pending) || (queue?.pool_available ?? 0) === 0), () => !queue?.current_assertion_id), () => !pinnedAssertionIdRef.current), () => !queueSettled);
            return pick(Boolean(streamConnected && !waitingForNextCard), () => {
                return;
            }, () => {
                const needsPoll = waitingForNextCard ||
                    (pick(Boolean(!queue?.document_complete), () => (!queue?.current_assertion_id ||
                        // The RAG window slides as the learner advances pages, resetting
                        // rag_window_ready to false until the new pages finish indexing. Keep
                        // polling until it flips back so the chat composer re-enables on its own
                        // instead of being stuck on "Preparing chat context…" until a refresh.
                        queue.rag_window_ready === false ||
                        (pick(Boolean(queue.generation_pending), () => (queue.pool_available ?? 0) === 0, () => Boolean(queue.generation_pending)))), () => !queue?.document_complete));
                return pick(Boolean(!needsPoll), () => {
                    return;
                }, () => {
                    const id = window.setInterval(() => {
                        void apiGet<McqState>(learnQueuePath)
                            .then((data) => {
                            return pick(Boolean(pinnedAssertionIdRef.current), () => {
                                const realNext = data.current_assertion_id;
                                pick(Boolean(realNext &&
                                    realNext !== pinnedAssertionIdRef.current &&
                                    !pendingNextAssertionIdRef.current), () => {
                                    pendingNextAssertionIdRef.current = realNext;
                                }, () => {
                                });
                                setQueue((prev) => mergeQueueState({ ...data, current_assertion_id: pinnedAssertionIdRef.current }, prev, answeredAssertionIdsRef.current));
                                return;
                            }, () => {
                                setQueue((prev) => mergeQueueState(data, prev, answeredAssertionIdsRef.current));
                            });
                        })
                            .catch(() => {
                        });
                    }, choose(Boolean(waitingForNextCard), 1500, 3000));
                    return () => window.clearInterval(id);
                });
            });
        });
    }, [
        artifactId,
        invalidArtifactId,
        studyRangeKey,
        artifact?.status,
        streamConnected,
        learnQueuePath,
        queue?.current_assertion_id,
        queue?.generation_pending,
        queue?.pool_available,
        queue?.document_complete,
        queue?.no_questions_reason,
        queue?.prompt_reselect_pages,
        queue?.rag_window_ready,
    ]);
    useEffect(() => {
        return pick(Boolean(invalidArtifactId), () => {
            return;
        }, () => pick(Boolean(queue?.current_assertion_id), () => pick(Boolean(pinnedAssertionIdRef.current), () => {
            return;
        }, () => {
            // Assertion advanced: reset answer chrome only. Keep stem/options on screen
            // until the next fetch lands — clearing them made hasQuestion false and
            // McqHeroPanel flashed the full loading ring for ~200–500ms.
            setSelected(null);
            setMultiSelected([]);
            setGradeState(null);
            setFeedback(null);
            setConfidence(null);
            return;
        }), () => pick(Boolean(pinnedAssertionIdRef.current), () => {
            return;
        }, () => {
            setOptions([]);
            setSelected(null);
            setGradeState(null);
            setFeedback(null);
            setConfidence(null);
        })));
    }, [invalidArtifactId, queue?.current_assertion_id]);
    useEffect(() => {
        return pick(Boolean(invalidArtifactId || !displayAssertionId), () => {
            return;
        }, () => pick(Boolean(assertionQuery.isError), () => {
            // eslint-disable-next-line react-hooks/set-state-in-effect -- intentionally reset grade/feedback when the study mode changes
            setQuestion("Could not load question.");
            setOptions([]);
            return;
        }, () => pick(Boolean(!assertionQuery.data || assertionQuery.isPlaceholderData), () => {
            return;
        }, () => pick(Boolean(pinnedAssertionIdRef.current), () => {
            return;
        }, () => {
            applyAssertionRowToMcq(assertionQuery.data, displayAssertionId);
        }))));
    }, [assertionQuery.data, assertionQuery.isError, assertionQuery.isPlaceholderData, displayAssertionId, invalidArtifactId]);
    // After a transient assertion 502, keep retrying until the card loads so Learn
    // does not stay on "Could not load question." until the learner refreshes.
    useEffect(() => {
        return pick(Boolean(!assertionQuery.isError || !displayAssertionId), () => {
            return;
        }, () => {
            const id = window.setInterval(() => {
                void assertionQuery.refetch();
            }, 2500);
            return () => window.clearInterval(id);
        });
    }, [assertionQuery.isError, displayAssertionId, assertionQuery.refetch]);
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- intentionally reset grade/feedback on the active question change
        setGradeState(null);
        setFeedback(null);
    }, [mode]);
    useEffect(() => {
        return pick(Boolean(!queue?.page_complete || queue.current_assertion_id || queue.document_complete), () => {
            return;
        }, () => {
            // Short debounce only — was 1.5s of dead air after the last question on a page.
            const id = window.setTimeout(() => {
                void refreshQueue();
            }, 200);
            return () => window.clearTimeout(id);
        });
    }, [queue?.page_complete, queue?.current_assertion_id, queue?.document_complete]);
    async function refreshQueue() {
        const data = await apiGet<McqState>(learnQueuePath);
        setQueue((prev) => {
            return pick(Boolean(pinnedAssertionIdRef.current), () => mergeQueueState({ ...data, current_assertion_id: pinnedAssertionIdRef.current }, prev, answeredAssertionIdsRef.current), () => mergeQueueState(data, prev, answeredAssertionIdsRef.current));
        });
    }
    async function setStudyMode(nextMode: "adaptive" | "classic") {
        return await pick(Boolean(queue?.study_mode === nextMode), async () => {
            return;
        }, async () => {
            // Optimistic - the change applies to the next question, no regeneration.
            setQueue((q) => (choose(Boolean(q), { ...q, study_mode: nextMode }, q)));
            try {
                await apiPost(`/api/artifacts/${artifactId}/study-mode`, { mode: nextMode });
            }
            catch {
                void refreshQueue();
            }
        });
    }
    // Surface the tutor composer (floating panel on desktop, tutor tab on mobile)
    // so a quoted passage is immediately visible and editable.
    function revealTutor() {
        pick(Boolean(isLg), () => {
            openTutor();
        }, () => {
            setMobileTutorFocus((n) => n + 1);
        });
    }
    function quoteSelectionToChat(text: string) {
        quoteToComposer(text);
        revealTutor();
    }
    function explainSelectionInChat(text: string) {
        revealTutor();
        askBuddy(`Explain this in simple terms:\n\n"${text}"\n\n`);
    }
    function wikipediaSelectionInChat(text: string) {
        revealTutor();
        void askBuddyWithWikipedia(text);
    }
    function handleMcqSelect(value: string) {/*..............................................................................*/
        pick(Boolean(gradeState && !gradeState.correct && mode === "learn"), () => {
            setGradeState(null);
            setFeedback(null);
        }, () => {
        });
        setSelected(value);
    }
    function handleMcqToggle(index: number) {/*..............................................................................*/
        pick(Boolean(gradeState && !gradeState.correct && mode === "learn"), () => {
            setGradeState(null);
            setFeedback(null);
        }, () => {
        });
        setMultiSelected((prev) => pick(Boolean(prev.includes(index)), () => prev.filter((i) => i !== index), () => [...prev, index].sort((a, b) => a - b)));
    }
    function applyAssertionRowToMcq(row: {
        id?: string;
        payload?: Record<string, unknown>;
        title?: string;
    }, expectedId: string) {/*..............................................................................*/
        return pick(Boolean(row.id != null && String(row.id) !== expectedId), () => false, () => {
            const p = (row.payload ?? {}) as AssertionPayload;
            setQuestion(formatMcqStemForDisplay(p.question ?? p.stem ?? row.title ?? "Question"));
            setOptions(normalizeMcqOptions(p.options, p.choices));
            setCurrentConcept((p.primary_concept ?? "").trim() || null);
            setIsMulti(p.is_multi === true ||
                (pick(Boolean(Array.isArray(p.correct_indices)), () => p.correct_indices.length >= 2, () => Array.isArray(p.correct_indices))));
            setMultiSelected([]);
            setSelected(null);
            setFeedback(null);
            setGradeState(null);
            return true;
        });
    }
    async function advanceMcq() {
        const __z6 = { hit: false, val: undefined as any };
        await pick(Boolean(advancingMcqRef.current), async () => {
            __z6.hit = true;
        }, async () => {
            advancingMcqRef.current = true;
            const answered = answeredAssertionIdsRef.current;
            const isStaleCurrent = (id: string | null | undefined) => !id || answered.has(id);
            try {
                const rawNext = pendingNextAssertionIdRef.current;
                const nextId = choose(Boolean(rawNext && !answered.has(rawNext)), rawNext, null);
                pinnedAssertionIdRef.current = null;
                setPinnedAssertionId(null);
                pendingNextAssertionIdRef.current = null;
                setGradeState(null);
                setFeedback(null);
                setSelected(null);
                setMultiSelected([]);
                await pick(Boolean(nextId), async () => {
                    const cached = queryClient.getQueryData<{
                        id?: string;
                        payload?: Record<string, unknown>;
                        title?: string;
                    }>(queryKeys.assertion(nextId));
                    pick(Boolean(cached), () => {
                        applyAssertionRowToMcq({ ...cached, id: nextId }, nextId);
                    }, () => {
                    });
                    // Instant Next: swap pointer; refresh pool metadata without regressing the card.
                    setQueue((q) => (choose(Boolean(q), { ...q, current_assertion_id: nextId }, q)));
                    void apiGet<McqState>(learnQueuePath)
                        .then((data) => {
                        return pick(Boolean(pinnedAssertionIdRef.current), () => {
                            return;
                        }, () => {
                            setQueue((prev) => mergeQueueState(data, prev, answered));
                        });
                    })
                        .catch(() => {
                    });
                }, async () => {
                    // Grade often finishes before the next card cooks. Poll briefly so we
                    // do not freeze on the wait ring if SSE is quiet.
                    setMcqLoading(true);
                    let data = await apiGet<McqState>(learnQueuePath);
                    for (let i = 0; i < 20 && isStaleCurrent(data.current_assertion_id) && !data.document_complete && !__z6.hit; i += 1) {
                        await new Promise((r) => window.setTimeout(r, 750));
                        await pick(Boolean(pinnedAssertionIdRef.current), async () => {
                            __z6.hit = true;
                        }, async () => {
                            data = await apiGet<McqState>(learnQueuePath);
                        });
                    }
                    pick(Boolean(!__z6.hit), () => {
                        pick(Boolean(!pinnedAssertionIdRef.current), () => {
                            setQueue((prev) => mergeQueueState(data, prev, answered));
                        }, () => {
                        });
                    }, () => {
                    });
                });
            }
            catch {
                setFeedback("Could not load next question.");
            }
            finally {
                advancingMcqRef.current = false;
                setMcqLoading(false);
            }
        });
    }
    async function submitMcq() {
        const hasSelection = choose(Boolean(isMulti), multiSelected.length > 0, selected !== null);
        return await pick(Boolean(!displayAssertionId || !hasSelection || submitting), async () => {
            return;
        }, async () => {
            const answeredId = displayAssertionId;
            answeredAssertionIdsRef.current.add(answeredId);
            // Pin before submitting so queue SSE cannot advance the visible card mid-grade.
            pinnedAssertionIdRef.current = answeredId;
            setPinnedAssertionId(answeredId);
            setSubmitting(true);
            const answeredSelection = pick(Boolean(isMulti), () => (multiSelected[0] ?? -1), () => Number(selected));
            // Snapshot stem/options/selection at submit time — queue can advance before SSE
            // finishes, and a stale render closure would pair the wrong text with answeredId.
            const stemSnapshot = question;
            const optionsSnapshot = [...options];
            const conceptSnapshot = currentConcept;
            const multiSnapshot = pick(Boolean(isMulti), () => [...multiSelected], () => undefined);
            setFeedback(null);
            return await pick(Boolean(studyingOffline && offlineDeck && offlinePack), async () => {
                const assertion = offlineDeck.find((a) => a.id === answeredId);
                await pick(Boolean(assertion), async () => {
                    const choiceIndex = pick(Boolean(isMulti), () => (multiSelected[0] ?? -1), () => Number(selected));
                    const choiceIndices = choose(Boolean(isMulti), multiSelected, null);
                    const verdict = localGrade(assertion, choiceIndex, choiceIndices);
                    setGradeState({
                        correct: verdict.correct,
                        correctIndex: verdict.correct_index,
                        correctIndices: verdict.correct_indices,
                    });
                    setFeedback(verdict.feedback || verdict.explanation || "Answer recorded.");
                    setAnsweredHistory((h) => {
                        const prior = h.find((c) => c.assertionId === answeredId);
                        return [
                            ...h.filter((c) => c.assertionId !== answeredId),
                            {
                                assertionId: answeredId,
                                stem: stemSnapshot,
                                options: optionsSnapshot,
                                selectedIndex: answeredSelection,
                                selectedIndices: multiSnapshot,
                                gradeState: {
                                    correct: verdict.correct,
                                    correctIndex: verdict.correct_index,
                                    correctIndices: verdict.correct_indices,
                                },
                                feedback: verdict.feedback || verdict.explanation,
                                concept: conceptSnapshot,
                                firstTryCorrect: pick(Boolean(prior), () => prior.firstTryCorrect, () => verdict.correct),
                            },
                        ];
                    });
                    // Queue for sync + advance the local pointer.
                    await enqueueGrade(offlinePack.id, {
                        assertionId: answeredId,
                        choiceIndex,
                        choiceIndices,
                        mode,
                    });
                    const answered = new Set(offlineAnsweredIds);
                    answered.add(answeredId);
                    setOfflineAnsweredIds(answered);
                    const nextId = nextAssertionId(offlineDeck, answered);
                    pendingNextAssertionIdRef.current = nextId;
                    pick(Boolean(navigator.onLine), () => {
                        void syncPack(offlinePack.id).catch(() => undefined);
                    }, () => {
                    });
                }, async () => {
                });
                setSubmitting(false);
                return;
            }, async () => {
                // Verdict-first stream: the outcome (index compare + stored explanation) lands
                // in ~200ms and reveals immediately; the LLM coaching follows as a second event.
                let gotVerdict = false;
                let verdictExplanation = "";
                try {
                    await apiPostSSE("/api/mcq/grade/stream", {
                        assertion_id: answeredId,
                        // choice_index carries the (first) chosen option for both paths; multi
                        // items also send the full chosen set via choice_indices.
                        choice_index: pick(Boolean(isMulti), () => (multiSelected[0] ?? -1), () => Number(selected)),
                        ...(choose(Boolean(isMulti), { choice_indices: multiSelected }, {})),
                        mode,
                        ...(choose(Boolean(mode === "learn" && confidence != null), { confidence }, {})),
                    }, {
                        onEvent: (event, data) => {
                            const __z8 = { hit: false, val: undefined as any };
                            pick(Boolean(event === "verdict"), () => {
                                let v: {
                                    correct?: boolean;
                                    correct_index?: number;
                                    correct_indices?: number[];
                                    explanation?: string;
                                };
                                try {
                                    v = JSON.parse(data);
                                }
                                catch {
                                    __z8.hit = true;
                                }
                                pick(Boolean(!__z8.hit), () => {
                                    gotVerdict = true;
                                    verdictExplanation = v.explanation ?? "";
                                    const correct = Boolean(v.correct);
                                    const correctIndex = v.correct_index ?? (pick(Boolean(isMulti), () => (multiSelected[0] ?? 0), () => Number(selected)));
                                    const correctIndices = v.correct_indices;
                                    // Reveal the outcome now - coaching is still on its way.
                                    setGradeState({ correct, correctIndex, correctIndices });
                                    setSubmitting(false);
                                    // Record the answer immediately (coaching text is patched in below).
                                    setAnsweredHistory((h) => {
                                        // Preserve the FIRST-attempt result across Learn-mode retries - the
                                        // report card grades you on the first try, not the eventual retry.
                                        const prior = h.find((c) => c.assertionId === answeredId);
                                        return [
                                            ...h.filter((c) => c.assertionId !== answeredId),
                                            {
                                                assertionId: answeredId,
                                                stem: stemSnapshot,
                                                options: optionsSnapshot,
                                                selectedIndex: answeredSelection,
                                                selectedIndices: multiSnapshot,
                                                gradeState: { correct, correctIndex, correctIndices },
                                                feedback: verdictExplanation,
                                                concept: conceptSnapshot,
                                                firstTryCorrect: pick(Boolean(prior), () => prior.firstTryCorrect, () => correct),
                                            },
                                        ];
                                    });
                                }, () => {
                                });
                            }, () => {
                                pick(Boolean(event === "next"), () => {
                                    let n: {
                                        next_assertion_id?: string;
                                    };
                                    try {
                                        n = JSON.parse(data);
                                    }
                                    catch {
                                        __z8.hit = true;
                                    }
                                    pick(Boolean(!__z8.hit), () => {
                                        const nextId = n.next_assertion_id;
                                        pick(Boolean(!nextId), () => {
                                            __z8.hit = true;
                                        }, () => {
                                            pendingNextAssertionIdRef.current = nextId;
                                            // Prefetch stem+options so Continue is one-frame instant.
                                            void queryClient.prefetchQuery({
                                                queryKey: queryKeys.assertion(nextId),
                                                queryFn: () => apiGet<{
                                                    payload: Record<string, unknown>;
                                                    title?: string;
                                                }>(`/api/assertions/${nextId}`),
                                                staleTime: 60000,
                                            });
                                        });
                                    }, () => {
                                    });
                                }, () => {
                                    pick(Boolean(event === "feedback"), () => {
                                        let f: {
                                            feedback?: string;
                                        };
                                        try {
                                            f = JSON.parse(data);
                                        }
                                        catch {
                                            __z8.hit = true;
                                        }
                                        pick(Boolean(!__z8.hit), () => {
                                            const text = (f.feedback || "").trim() || verdictExplanation;
                                            pick(Boolean(!text), () => {
                                                __z8.hit = true;
                                            }, () => {
                                                setFeedback(text);
                                                setAnsweredHistory((h) => h.map((c) => (choose(Boolean(c.assertionId === answeredId), { ...c, feedback: text }, c))));
                                            });
                                        }, () => {
                                        });
                                    }, () => {
                                    });
                                });
                            });
                        },
                    });
                    // Stream ended without ever producing coaching - fall back so the "writing…"
                    // affordance resolves instead of hanging.
                    setFeedback((prev) => prev ?? (verdictExplanation || "Answer recorded."));
                }
                catch {
                    pick(Boolean(!gotVerdict), () => {
                        pinnedAssertionIdRef.current = null;
                        setPinnedAssertionId(null);
                        setFeedback("Could not grade answer - try again.");
                    }, () => {
                        setFeedback((prev) => prev ?? (verdictExplanation || "Answer recorded."));
                    });
                }
                finally {
                    setSubmitting(false);
                }
            });
        });
    }
    async function confirmRange(prepMode: "now" | "background" = "now") {
        return await pick(Boolean(sortedSelection.length === 0), async () => {
            setSetupError("Select at least one page to study.");
            return;
        }, async () => {
            setConfirming(true);
            setConfirmingMode(prepMode);
            setSetupError(null);
            try {
                const from = sortedSelection[0];
                const to = sortedSelection[sortedSelection.length - 1];
                await apiPost(`/api/artifacts/${artifactId}/page-range`, {
                    from,
                    to,
                    pages: sortedSelection,
                    prep_mode: prepMode,
                });
                const updated = await apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`);
                queryClient.setQueryData(queryKeys.artifact(artifactId), updated);
                setArtifact(updated);
                void queryClient.invalidateQueries({ queryKey: queryKeys.artifactPages(artifactId) });
                setReselectOpen(false);
                pick(Boolean(prepMode === "now"), () => {
                    setQueue(null);
                    setMcqLoading(true);
                    setQuestion("Loading questions…");
                    setOptions([]);
                    setSelected(null);
                    setGradeState(null);
                    setFeedback(null);
                    setAnsweredHistory([]);
                    setReviewIndex(null);
                    answeredAssertionIdsRef.current = new Set();
                }, () => {
                });
            }
            catch (e) {
                setSetupError(choose(Boolean(e instanceof Error), e.message, "Could not start indexing"));
            }
            finally {
                setConfirming(false);
                setConfirmingMode(null);
            }
        });
    }
    function openReselectPages() {
        return pick(Boolean(isNewspaper), () => {
            router.push(newspaperBackHref);
            return;
        }, () => pick(Boolean(!selectedRange), () => {
            return;
        }, () => {
            const suggestion = suggestNextPageRange(selectedRange, pageCount);
            setSelectedPages(pagesInRange(suggestion.from, suggestion.to));
            setLastClickedPage(suggestion.from);
            setSetupError(null);
            setReselectOpen(true);
        }));
    }
    const newspaperLearnComplete = Boolean(queue?.learn_complete);
    const newspaperTestReady = Boolean(queue?.test_pool_ready);
    function handleStudyModeChange(next: StudyMode) {/*..............................................................................*/
        return pick(Boolean(isNewspaper && !newspaperLearnComplete && next === "test"), () => {
            return;
        }, () => {
            setMode(next);
        });
    }
    function startNewspaperTest() {
        setAnsweredHistory([]);
        setReviewIndex(null);
        setSelected(null);
        setMultiSelected([]);
        setGradeState(null);
        setFeedback(null);
        setQuestion("Loading questions…");
        setOptions([]);
        setMcqLoading(true);
        pinnedAssertionIdRef.current = null;
        setPinnedAssertionId(null);
        pendingNextAssertionIdRef.current = null;
        answeredAssertionIdsRef.current = new Set();
        setMode("test");
        router.replace(`/workspace/${artifactId}?mode=test`);
    }
    function handleRangeChange(from: number, to: number) {
        const lo = Math.min(from, to);
        const hi = Math.max(from, to);
        setSelectedPages(pagesInRange(lo, hi));
        setLastClickedPage(lo);
    }
    function handlePageToggle(page: number, shiftKey: boolean) {
        setSelectedPages((prev) => {/*..............................................................................*/
            const next = new Set(prev);
            pick(Boolean(shiftKey && lastClickedPage !== null), () => {
                const lo = Math.min(lastClickedPage, page);
                const hi = Math.max(lastClickedPage, page);
                for (let p = lo; p <= hi; p += 1) {
                    next.add(p);
                }
            }, () => {
                pick(Boolean(next.has(page)), () => {
                    next.delete(page);
                }, () => {
                    next.add(page);
                });
            });
            return [...next].sort((a, b) => a - b);
        });
        setLastClickedPage(page);
    }
    return pick(Boolean(invalidArtifactId), () => (<Center mih="50vh">
        <Stack gap="sm" w="100%" maw={280}>
          <Skeleton height={24} radius="md"/>
          <Skeleton height={120} radius="md"/>
        </Stack>
      </Center>), () => pick(Boolean(setupError && !artifact), () => (<Center mih="50vh">
        <Stack align="center" gap="md" maw={420}>
          <Alert color="terracotta" title="Could not load source" variant="light">
            {setupError}
          </Alert>
          <Button variant="default" leftSection={<IconArrowLeft size={16}/>} onClick={() => router.push(choose(Boolean(isNewspaper), newspaperBackHref, "/workspace"))}>
            {choose(Boolean(isNewspaper), "Back to paper", "Back to library")}
          </Button>
        </Stack>
      </Center>), () => pick(Boolean(!artifact || !pages), () => (<Center mih="50vh" px="sm">
        <Stack gap="sm" w="100%" maw={320}>
          <Skeleton height={28} width="60%" radius="md"/>
          <Skeleton height={200} radius="md"/>
          <Skeleton height={16} width="40%" radius="md"/>
        </Stack>
      </Center>), () => pick(Boolean(!selectedRange), () => pick(Boolean(isNewspaper), () => (<Center mih="50vh">
          <Stack gap="sm" w="100%" maw={320}>
            <Skeleton height={28} width="60%" radius="md"/>
            <Skeleton height={200} radius="md"/>
          </Stack>
        </Center>), () => {
        const shortName = artifact.filename?.replace(/\.[^.]+$/, "") ?? "Source";
        return (<PageSelectionScreen subtitle={`${shortName} · ${choose(Boolean(pageCount === 1), "1 page", `${pageCount} pages`)}`} pageCount={pageCount} sliderFrom={sliderFrom} sliderTo={sliderTo} sliderMarks={sliderMarks} selectedPages={sortedSelection} isDark={isDark} isPdf={isPdf} pdfDoc={pdfDoc} pdfLoading={pdfLoading} pdfError={pdfError} pageTexts={pageTexts} pageTextsLoading={pageTextsLoading} thumbCanvasRefs={thumbCanvasRefs} confirming={confirming} confirmingMode={confirmingMode} setupError={setupError} isCompact={isNarrow} onRangeChange={handleRangeChange} onPageToggle={handlePageToggle} onSelectAll={() => {
                setSelectedPages(pagesInRange(1, pageCount));
                setLastClickedPage(1);
            }} onClearAll={() => {
                setSelectedPages([]);
                setLastClickedPage(null);
            }} onConfirmNow={() => void confirmRange("now")} onPrepInBackground={() => void confirmRange("background")}/>);
    }), () => pick(Boolean(artifact.status === "failed"), () => (<Center mih="70vh">
        <Paper withBorder p="xl" maw={480} w="100%">
          <Stack gap="md" align="center">
            <Title order={3} style={{ fontFamily: "var(--font-serif), Georgia, serif", fontWeight: 500 }}>
              Indexing failed
            </Title>
            <Text c="dimmed" ta="center" lh={1.6}>
              We could not finish preparing pages {selectedRange?.from}-{selectedRange?.to}. Try a smaller
              range or upload the file again.
            </Text>
            <Button variant="default" onClick={() => window.location.reload()}>
              Reload
            </Button>
          </Stack>
        </Paper>
      </Center>), () => pick(Boolean(artifact.status === "indexing" ||
        artifact.status === "prepping" ||
        isBackgroundPrepActive(artifact.meta)), () => {
        const prepProgress = artifact.meta?.prep_progress as PrepProgress | undefined;
        const backgroundPrep = isBackgroundPrepActive(artifact.meta);
        const progress = prepProgressPercent(artifact.status, prepProgress, artifact.index_progress ?? 0);
        const prepStage = pick(Boolean(backgroundPrep), () => prepScreenStatus(prepProgress, Math.floor(Date.now() / 4000)), () => null);
        const indexStage = pick(Boolean(backgroundPrep), () => null, () => indexingStage(artifact.index_progress ?? 0));
        const title = prepStage?.title ?? indexStage?.title ?? "Preparing";
        const detail = prepStage?.detail ?? indexStage?.detail ?? "Getting your source ready";
        const phaseLabel = prepStage?.phaseLabel ?? "Progress";
        return (<Center flex={1} px="sm">
        <Paper withBorder p={{ base: "lg", sm: "xl" }} maw={520} w="100%">
          <Stack gap="lg">
            <Stack gap="xs" align="center">
              <PetPlayground height={130} count={1} wander style={{ width: 400, maxWidth: "100%" }}/>
              <Text size="lg" fw={500} ta="center" style={{ letterSpacing: "-0.02em", fontFamily: "var(--font-serif), Georgia, serif" }}>
                {title}
              </Text>
              <Text c="dimmed" ta="center" size="sm">
                {detail}
              </Text>
              {choose(Boolean(backgroundPrep), (<Text size="xs" c="dimmed" ta="center" lh={1.5} maw={360}>
                  We are indexing and cooking questions for your whole selection. Come back when you are ready to study.
                </Text>), null)}
              {choose(Boolean(artifact.filename), (<Text size="xs" c="dimmed" ta="center" opacity={0.7}>
                  {artifact.filename}
                </Text>), null)}
            </Stack>
            <Stack gap="xs">
              <Group justify="space-between">
                <Text size="sm" fw={500}>
                  {choose(Boolean(backgroundPrep), phaseLabel, "Progress")}
                </Text>
                <Text size="sm" c="dimmed">
                  {progress}%
                </Text>
              </Group>
              <Progress value={progress} size="md" radius="xl" color="lavender" animated={progress < 100}/>
            </Stack>
          </Stack>
        </Paper>
      </Center>);
    }, () => {
        const shortFilename = pick(Boolean(isNewspaper), () => (paperTitle || artifact.filename?.replace(/\.[^.]+$/, "") || "Newspaper"), () => (artifact.filename?.replace(/\.[^.]+$/, "") ?? "Source"));
        const questionIndex = choose(Boolean(isNewspaper), (queue?.current_page_question_number ??
            (queue?.edition_page_questions_answered ?? 0) + (choose(Boolean(queue?.current_assertion_id), 1, 0))), (queue?.question_number ?? (queue?.questions_answered ?? 0) + 1));
        const questionTotal = choose(Boolean(isNewspaper), (queue?.edition_page_question_total ?? 0), (queue?.question_budget ?? queue?.max_per_page ?? 0));
        const editionQuestionIndex = choose(Boolean(isNewspaper), (queue?.question_number ?? (queue?.questions_answered ?? 0) + 1), undefined);
        const editionQuestionTotal = choose(Boolean(isNewspaper), (queue?.edition_question_total ?? queue?.questions_generated ?? 0), undefined);
        const showPageComplete = pick(Boolean(!isNewspaper), () => pick(Boolean(queue?.page_complete), () => pick(Boolean(!queue?.current_assertion_id), () => !queue?.document_complete, () => !queue?.current_assertion_id), () => Boolean(queue?.page_complete)), () => !isNewspaper);
        const showDocumentComplete = pick(Boolean(queue?.document_complete), () => !reselectOpen, () => Boolean(queue?.document_complete));
        const showNoQuestions = pick(Boolean(queue?.no_questions_reason), () => !reselectOpen, () => Boolean(queue?.no_questions_reason));
        // Newspaper editions are fixed-range — no page reselect UI.
        const showPromptReselect = pick(Boolean(!isNewspaper), () => pick(Boolean(queue?.prompt_reselect_pages), () => !reselectOpen, () => Boolean(queue?.prompt_reselect_pages)), () => !isNewspaper);
        const reselectPromptCopy = choose(Boolean(queue?.prompt_reselect_reason === "unreadable_content"), {
            title: "We can see content — but can't study it",
            body: "This looks full, but we can't study it as text. Choose pages with selectable text.",
        }, {
            title: "These pages look empty",
            body: "Several pages here have no readable content. See content on these pages, or are they blank? Pick only pages with actual text to study.",
        });
        // Test mode is summative: hold all feedback until the set is finished, then show
        // one score + a full review. (Learn keeps its encouraging per-page completion.)
        const testCorrect = answeredHistory.filter((c) => c.gradeState.correct).length;
        const showTestResults = pick(Boolean(mode === "test"), () => pick(Boolean(showDocumentComplete), () => answeredHistory.length > 0, () => showDocumentComplete), () => mode === "test");
        const currentPage = queue?.current_page ?? 0;
        const answeredOnCurrentPage = choose(Boolean(isNewspaper), (queue?.edition_page_questions_answered ?? 0), (queue?.answered_on_page ?? 0));
        const hasAnsweredOnCurrentPage = answeredOnCurrentPage > 0;
        const lessonDismissedForPage = lessonDismissed.has(currentPage);
        const lessonForcedOpen = lessonForcedOpenPage === currentPage;
        // Learn-only: show the page's pre-question lesson once per page, before the
        // MCQ hero. Suppressed in Test mode (you can't pre-teach a test), when the
        // page/document is complete, once the learner has dismissed it for this page,
        // and when they already have MCQ progress on this page (resume). Deliberately
        // NOT gated on !isNewspaper — newspaper Learn shows lessons too (unlike
        // showPageComplete above). Anything but a ready lesson falls through.
        const showLesson = pick(Boolean(mode === "learn"), () => pick(Boolean(queue?.page_lesson?.status === "ready"), () => pick(Boolean(queue?.page_lesson?.body), () => pick(Boolean((lessonForcedOpen ||
            (!lessonDismissedForPage && !hasAnsweredOnCurrentPage))), () => pick(Boolean(!showPageComplete), () => pick(Boolean(!showDocumentComplete), () => !showTestResults, () => !showDocumentComplete), () => !showPageComplete), () => (lessonForcedOpen ||
            (pick(Boolean(!lessonDismissedForPage), () => !hasAnsweredOnCurrentPage, () => !lessonDismissedForPage)))), () => Boolean(queue?.page_lesson?.body)), () => queue?.page_lesson?.status === "ready"), () => mode === "learn");
        // Learn-only meta-bar pill: re-open a lesson the learner has dismissed for
        // the current page. Visible only while the MCQ hero is showing (not on the
        // lesson screen itself, nor on page/document-complete screens).
        const lessonReadyForPage = pick(Boolean(showLesson), () => false, () => Boolean(pick(Boolean(mode === "learn"), () => pick(Boolean(queue?.page_lesson?.status === "ready"), () => pick(Boolean(queue?.page_lesson?.body), () => pick(Boolean((lessonDismissedForPage || hasAnsweredOnCurrentPage)), () => pick(Boolean(!showPageComplete), () => pick(Boolean(!showDocumentComplete), () => !showTestResults, () => !showDocumentComplete), () => !showPageComplete), () => (lessonDismissedForPage || hasAnsweredOnCurrentPage)), () => Boolean(queue?.page_lesson?.body)), () => queue?.page_lesson?.status === "ready"), () => mode === "learn")));
        const dismissLessonForPage = (page: number) => {
            setLessonForcedOpenPage(null);
            setLessonDismissedPages((prev) => pick(Boolean(prev.includes(page)), () => prev, () => [...prev, page].sort((a, b) => a - b)));
        };
        const reopenLesson = () => {
            setLessonForcedOpenPage(currentPage);
            setLessonDismissedPages((prev) => prev.filter((p) => p !== currentPage));
        };
        const completedRange = selectedRange;
        const nextRangeSuggestion = pick(Boolean(completedRange), () => suggestNextPageRange(completedRange, pageCount), () => null);
        // Both edges carry a floating trigger (SOURCE on the left, Zivo on the right), so a
        // pinned column has to leave room for one or it slides underneath.
        // Newspaper has no Source trigger on the left, so left-pin uses a smaller inset.
        const pinned = choose(Boolean(isNarrow), null, choose(Boolean(studyAlign === "left"), "left", choose(Boolean(studyAlign === "right"), "right", null)));
        const pinInsetLeft = choose(Boolean(isNewspaper), 16, 48);
        const floatingLane = computeFloatingLaneInsets([
            {
                open: sourceOpen,
                minimized: sourceGeometry?.minimized ?? false,
                maximized: sourceGeometry?.maximized ?? false,
                x: sourceGeometry?.rect.x ?? 0,
                w: sourceGeometry?.rect.w ?? 0,
            },
            {
                open: pick(Boolean(tutorOpen), () => mode !== "test", () => tutorOpen),
                minimized: tutorGeometry?.minimized ?? false,
                maximized: tutorGeometry?.maximized ?? false,
                x: tutorGeometry?.rect.x ?? 0,
                w: tutorGeometry?.rect.w ?? 0,
            },
        ], studyRowWidth);
        const newspaperContextLabel = pick(Boolean(isNewspaper), () => [paperTitle || shortFilename, editionDate].filter(Boolean).join(" · "), () => null);
        const tutorNewspaperHint = choose(Boolean(isNewspaper && questionSourcePage), `Page ${questionSourcePage} of ${paperTitle || "today's edition"}${choose(Boolean(editionDate), ` (${editionDate})`, "")}. Ask about this question — I'll use that page's text.`, choose(Boolean(isNewspaper), `Ask about ${paperTitle || "today's paper"}${choose(Boolean(editionDate), ` (${editionDate})`, "")}. I know which edition page each question came from.`, undefined));
        const reviewableCount = Math.max(answeredHistory.length, queue?.questions_answered ?? 0);
        const studyMetaBar = (<StudyMetaBar questionIndex={questionIndex} questionTotal={questionTotal} page={queue?.current_page} mode={mode} onModeChange={handleStudyModeChange} showProgress={pick(Boolean((mode === "learn" || mode === "test")), () => pick(Boolean(!mcqLoading), () => pick(Boolean(displayAssertionId), () => pick(Boolean(!showPageComplete), () => !showDocumentComplete, () => !showPageComplete), () => Boolean(displayAssertionId)), () => !mcqLoading), () => (mode === "learn" || mode === "test"))} compact={isNarrow} showModeSelect={isCompact} studyMode={queue?.study_mode} onStudyModeChange={(m) => void setStudyMode(m)} align={studyAlign} onAlignChange={handleStudyAlignChange} contextLabel={newspaperContextLabel} isNewspaper={isNewspaper} editionIndex={editionQuestionIndex} editionTotal={editionQuestionTotal} editionAnswered={queue?.questions_answered ?? 0} backHref={choose(Boolean(isNewspaper), newspaperBackHref, undefined)} backLabel="Days" showLessonPill={lessonReadyForPage} onReopenLesson={reopenLesson} selectionReason={choose(Boolean(mode === "learn"), (queue?.selection_reason ?? null), null)}/>);
        async function openReviewPrevious() {
            const __z10 = { hit: false, val: undefined as any };
            let history = answeredHistory;
            await pick(Boolean(history.length === 0 && reviewableCount > 0), async () => {
                try {
                    const data = await apiGet<{
                        items: LearnAnsweredItem[];
                    }>(`/api/artifacts/${artifactId}/learn-answered`);
                    history = learnAnsweredToCards(data.items ?? []);
                    pick(Boolean(history.length > 0), () => {
                        setAnsweredHistory(history);
                    }, () => {
                    });
                }
                catch {
                    __z10.hit = true;
                }
                pick(Boolean(history.length === 0), () => {
                    __z10.hit = true;
                }, () => {
                    const idx = pick(Boolean(gradeState), () => Math.max(0, history.length - 2), () => history.length - 1);
                    setReviewIndex(idx);
                });
            }, async () => {
                pick(Boolean(history.length === 0), () => {
                    __z10.hit = true;
                }, () => {
                    const idx = pick(Boolean(gradeState), () => Math.max(0, history.length - 2), () => history.length - 1);
                    setReviewIndex(idx);
                });
            });
        }
        const questionColumn = (<Box flex={1} mih={0} miw={0} h="100%" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <Box flex={1} mih={0} miw={0} style={{
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                minHeight: 0,
                paddingLeft: floatingLane.left,
                paddingRight: floatingLane.right,
            }}>
        <Box w="100%" maw={MCQ_CONTENT_MAX} miw={0} flex={1} mih={0} px={{ base: "sm", sm: "md", lg: "lg" }} style={{
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                alignSelf: choose(Boolean(pinned === "left"), "flex-start", choose(Boolean(pinned === "right"), "flex-end", "center")),
                marginLeft: choose(Boolean(pinned === "left"), pinInsetLeft, undefined),
                marginRight: choose(Boolean(pinned === "right"), (choose(Boolean(tutorOpen), 0, 48)), choose(Boolean(pinned === "left"), 0, undefined)),
            }}>
      <Box flex={1} mih={0} pb={{ base: "xs", sm: "md" }} style={{
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                justifyContent: "flex-start",
                paddingTop: choose(Boolean(isNarrow), 4, 8),
                minHeight: 0,
            }}>
        <Box mih={0} miw={0} px={4} style={{
                flex: 1,
                maxHeight: "100%",
                overflowY: "auto",
                overflowX: "hidden",
                overscrollBehavior: "contain",
                scrollbarGutter: "stable",
            }}>
          {pick(Boolean(mode === "brainstorm"), () => (<BrainstormView artifactId={artifact.id} compact={isNarrow}/>), () => pick(Boolean(mode === "explain"), () => (<ExplainView artifactId={artifact.id} compact={isNarrow}/>), () => pick(Boolean(mode === "notes"), () => (<NotesView artifactId={artifact.id} compact={isNarrow}/>), () => pick(Boolean(mode === "cards"), () => (<FlashcardsView artifactId={artifact.id} compact={isNarrow}/>), () => pick(Boolean(mode === "palace"), () => (<MemoryPalaceView artifactId={artifact.id} compact={isNarrow}/>), () => pick(Boolean(mode === "quiz"), () => (<QuizBuilderView artifactId={artifact.id} compact={isNarrow}/>), () => pick(Boolean(mode === "interview"), () => (<InterviewView artifactId={artifact.id} compact={isNarrow}/>), () => pick(Boolean(mode === "mains"), () => (<MainsView artifactId={artifact.id} compact={isNarrow}/>), () => pick(Boolean(mode === "coding"), () => (<CodingView artifactId={artifact.id} compact={isNarrow}/>), () => pick(Boolean(mode === "resume"), () => (<ResumeView artifactId={artifact.id} compact={isNarrow}/>), () => pick(Boolean(mode === "progress"), () => (<ProgressView artifactId={artifact.id} compact={isNarrow} onStartLearn={() => setMode("learn")} onStudyConcept={(concept) => {
                    void apiPost(`/api/artifacts/${artifact.id}/focus-concept`, { concept }).finally(() => {
                        setMode("learn");
                        void refreshQueue();
                    });
                }}/>), () => pick(Boolean(showPromptReselect), () => (<Stack align="center" gap="sm" py="xl" ta="center">
              <Text ff="var(--font-serif)" fz={choose(Boolean(isNarrow), 22, 28)} fw={500} c="var(--mantine-color-text)">
                {reselectPromptCopy.title}
              </Text>
              <Text c="dimmed" maw={420}>
                {reselectPromptCopy.body}
              </Text>
              <Button variant="light" color="lavender" radius="xl" mt="xs" onClick={openReselectPages}>
                Choose pages
              </Button>
            </Stack>), () => pick(Boolean(showNoQuestions), () => (<Stack align="center" gap="sm" py="xl" ta="center">
              <Text ff="var(--font-serif)" fz={choose(Boolean(isNarrow), 22, 28)} fw={500} c="var(--mantine-color-text)">
                Nothing to quiz here
              </Text>
              <Text c="dimmed" maw={420}>
                {choose(Boolean(isNewspaper), "This edition doesn’t have testable questions yet. Try another day.", "This material doesn’t contain testable content - it looks like a cover page, contents, or reference list. Choose different pages to study.")}
              </Text>
              <Button variant="light" color="lavender" radius="xl" mt="xs" leftSection={choose(Boolean(isNewspaper), <IconArrowLeft size={16}/>, undefined)} onClick={choose(Boolean(isNewspaper), () => router.push(newspaperBackHref), openReselectPages)}>
                {choose(Boolean(isNewspaper), "Back to days", "Choose pages")}
              </Button>
            </Stack>), () => pick(Boolean(showTestResults), () => (<TestResultsScreen correct={testCorrect} total={answeredHistory.length} answered={answeredHistory} report={studyReportQuery.data} compact={isNarrow} canChoosePages={pick(Boolean(!isNewspaper), () => Boolean(completedRange), () => !isNewspaper)} onReview={() => setReviewIndex(0)} onChoosePages={openReselectPages}/>), () => pick(Boolean(showDocumentComplete && completedRange), () => (<DocumentCompleteScreen completedFrom={completedRange.from} completedTo={completedRange.to} pageCount={pageCount} bookFinished={choose(Boolean(isNewspaper), true, (nextRangeSuggestion?.bookFinished ?? false))} nextFrom={nextRangeSuggestion?.from} nextTo={nextRangeSuggestion?.to} answered={answeredHistory} report={studyReportQuery.data} compact={isNarrow} onChoosePages={openReselectPages} actionLabel={choose(Boolean(isNewspaper), "Back to days", undefined)} hideNextSuggestion={isNewspaper} onContinueToTest={/*..............................................................................*/choose(Boolean(isNewspaper && mode === "learn" && newspaperLearnComplete && newspaperTestReady), startNewspaperTest, undefined)}/>), () => pick(Boolean(showPageComplete), () => (<PageCompleteInterstitial page={queue?.current_page ?? 0} compact={isNarrow} generating={Boolean(queue?.generation_pending)}/>), () => pick(Boolean(reviewIndex !== null && answeredHistory[reviewIndex]), () => (
            // Reviewing a previously-answered question - read-only, with the learner's
            // choice + the correct answer + explanation, and step controls.
            <Box style={{ height: "100%", minHeight: 0, width: "100%", overflow: "hidden" }}>
          <SelectionQuote onAsk={quoteSelectionToChat} onExplain={explainSelectionInChat} onWikipedia={wikipediaSelectionInChat}>
          <McqReviewView card={answeredHistory[reviewIndex]} index={reviewIndex} total={answeredHistory.length} compact={isNarrow} onPrev={choose(Boolean(reviewIndex > 0), () => setReviewIndex(reviewIndex - 1), undefined)} onNext={() => setReviewIndex(choose(Boolean(reviewIndex + 1 < answeredHistory.length), reviewIndex + 1, null))} onExit={() => setReviewIndex(null)}/>
          </SelectionQuote>
          </Box>), () => pick(Boolean(showLesson && queue?.page_lesson), () => (
            // Learn-only pre-question lesson: the learner reads the page's teaching
            // prose, then "Start the questions" dismisses it for this page and the
            // MCQ hero renders on the next pass.
            <SelectionQuote onAsk={quoteSelectionToChat} onExplain={explainSelectionInChat} onWikipedia={wikipediaSelectionInChat} disabled={queue.page_lesson.status === "generating" || !queue.page_lesson.body}>
            <LessonScreen title={queue.page_lesson.title} body={queue.page_lesson.body} status={queue.page_lesson.status} onStart={() => dismissLessonForPage(currentPage)}/>
          </SelectionQuote>), () => (
            // Fill the study area: stem, options, and feedback scroll together as one page.
            <Box style={{ height: "100%", minHeight: 0, width: "100%", overflow: "hidden" }}>
          <SelectionQuote onAsk={quoteSelectionToChat} onExplain={explainSelectionInChat} onWikipedia={wikipediaSelectionInChat} disabled={mcqLoading || !displayAssertionId}>
          <McqHeroPanel cardKey={displayAssertionId} stem={stem} options={options} selected={selected} onSelect={handleMcqSelect} multiSelect={isMulti} selectedIndices={multiSelected} onToggle={handleMcqToggle} feedback={feedback} mcqLoading={mcqLoading} artifactStatus={artifact.status} indexProgress={artifact.index_progress} hasQuestion={pick(Boolean(displayAssertionId), () => options.length > 0, () => Boolean(displayAssertionId))} queue={queue} mode={mode as "learn" | "test"} gradeState={gradeState} confidence={confidence} onConfidenceChange={setConfidence} focusConcept={choose(Boolean(mode === "learn"), currentConcept, null)} onFocusConcept={/*..............................................................................*/choose(Boolean(mode === "learn" && currentConcept), (concept) => {
                    void apiPost(`/api/artifacts/${artifactId}/focus-concept`, { concept }).then(() => {
                        void refreshQueue();
                        notifications.show({
                            title: "Focus set",
                            message: `Questions on “${concept}” will come up first.`,
                            color: "lavender",
                        });
                    });
                }, undefined)} submitting={submitting} compact={isNarrow} canReview={choose(Boolean(gradeState), reviewableCount >= 2, reviewableCount >= 1)} onReviewPrevious={() => void openReviewPrevious()} onSubmit={() => void submitMcq()} onContinue={() => void advanceMcq()} onRetry={() => {
                    pick(Boolean(assertionQuery.isError), () => {
                        void assertionQuery.refetch();
                    }, () => {
                        void refreshQueue();
                    });
                }} isNewspaper={isNewspaper} flagged={Boolean(pick(Boolean(displayAssertionId), () => flaggedIds[displayAssertionId], () => displayAssertionId))} flagBusy={flagBusy} onFlagQuestion={choose(Boolean(displayAssertionId), (reason) => {
                    const aid = displayAssertionId;
                    setFlagBusy(true);
                    void apiPost(`/api/assertions/${aid}/flag`, { reason })
                        .then(() => setFlaggedIds((m) => ({ ...m, [aid]: true })))
                        .catch(() => undefined)
                        .finally(() => setFlagBusy(false));
                }, undefined)}/>
          </SelectionQuote>
          </Box>)))))))))))))))))))}
        </Box>
      </Box>
        </Box>
      </Box>
    </Box>);
        return pick(Boolean(mode === "read"), () => pick(Boolean(isNewspaper), () => (<Center flex={1} px="md">
          <Stack align="center" gap="md" maw={420} ta="center">
            <Text c="dimmed" size="sm">
              Opening Learn…
            </Text>
            <Button variant="subtle" color="gray" leftSection={<IconArrowLeft size={16}/>} onClick={() => router.push(newspaperBackHref)}>
              Back to days
            </Button>
          </Stack>
        </Center>), () => {
            const notes = savedNotesQuery.data?.notes ?? [];
            // Stack reader/buddy into tabs whenever we're not in the desktop 3-pane (< 992),
            // matching how the rest of the study view drops to the single-column shell.
            const stacked = !isLg;
            return (<Box flex={1} mih={0} h="100%" pos="relative" mx={{ base: "calc(-1 * var(--mantine-spacing-xs))", sm: "calc(-1 * var(--mantine-spacing-md))" }} my={{ base: "calc(-1 * var(--mantine-spacing-xs))", sm: "calc(-1 * var(--mantine-spacing-md))" }} style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* Desktop 3-pane: the top was empty wasted space - float "Saved notes" into
                    the corner so the reader + buddy use the full height. */}
        {pick(Boolean(!stacked), () => (<Button size="xs" variant={choose(Boolean(readerNotesOpen), "light", "default")} color="lavender" radius="xl" leftSection={<IconNotebook size={14}/>} onClick={() => setReaderNotesOpen((o) => !o)} style={{ position: "absolute", top: 10, right: 16, zIndex: 20, boxShadow: "var(--mantine-shadow-sm)" }}>
            Saved notes{choose(Boolean(notes.length), ` (${notes.length})`, "")}
          </Button>), () => !stacked)}
        <Box flex={1} mih={0} h="100%" style={{ display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
        {studyMetaBar}
        {choose(Boolean(stacked), (<Group justify="flex-end" px={{ base: "sm", sm: "md" }} pt={6} pb={6} style={{ flexShrink: 0 }}>
            <Button size="xs" variant={choose(Boolean(readerNotesOpen), "light", "subtle")} color="lavender" radius="xl" leftSection={<IconNotebook size={14}/>} onClick={() => setReaderNotesOpen((o) => !o)}>
              Saved notes{choose(Boolean(notes.length), ` (${notes.length})`, "")}
            </Button>
          </Group>), null)}
        {choose(Boolean(stacked), (<SegmentedControl fullWidth size="xs" radius="md" mx="sm" mb={6} value={readerMobileTab} onChange={(v) => setReaderMobileTab(v as "reader" | "buddy")} data={[
                        { label: "Reader", value: "reader" },
                        { label: "Study buddy", value: "buddy" },
                    ]} style={{ flexShrink: 0 }}/>), null)}
        <Box flex={1} mih={0} style={{ display: "flex", overflow: "hidden" }}>
          {/* Reader: 75% column on desktop; full width with a tab toggle below 992. */}
          <Box flex={choose(Boolean(stacked), undefined, 3)} mih={0} w={choose(Boolean(stacked), "100%", undefined)} style={{ overflow: "hidden", display: choose(Boolean(!stacked || readerMobileTab === "reader"), "block", "none") }}>
            <PdfReader artifactId={artifact.id} pdfDoc={pdfDoc} isPdf={isPdf} pageCount={pageCount} onQuote={(t) => {
                quoteToComposer(t);
                pick(Boolean(stacked), () => {
                    setReaderMobileTab("buddy");
                }, () => {
                });
            }} onAsk={(m) => {
                askBuddy(m);
                pick(Boolean(stacked), () => {
                    setReaderMobileTab("buddy");
                }, () => {
                });
            }} onWikipedia={(t) => {
                    pick(Boolean(stacked), () => {
                        setReaderMobileTab("buddy");
                    }, () => {
                    });
                    revealTutor();
                    void askBuddyWithWikipedia(t);
                }} onSaveQuote={(t) => saveNote(t, t)}/>
          </Box>
          <Box flex={choose(Boolean(stacked), undefined, 1)} mih={0} miw={choose(Boolean(stacked), undefined, 300)} w={choose(Boolean(stacked), "100%", undefined)} style={{
                    borderLeft: choose(Boolean(stacked), undefined, "1px solid var(--app-border, var(--mantine-color-gray-2))"),
                    display: choose(Boolean(!stacked || readerMobileTab === "buddy"), "flex", "none"),
                    flexDirection: "column",
                    maxWidth: choose(Boolean(stacked), undefined, 460),
                }}>
            <TutorPanel messages={chatMessages} input={chatInput} busy={chatBusy} contextReady={chatContextReady} onInputChange={setChatInput} onSend={() => void sendChat()} onStop={stopChat} onRegenerate={regenerateChat} onEditUser={editChatFromUser} onClear={() => void clearChat()} onSaveNote={(c) => saveNote(c)} suggestions={READ_CHAT_SUGGESTIONS} emptyHint="Read on the left. Highlight anything to ask about it, or start here:" showHeader {...chatMcqProps}/>
          </Box>
        </Box>
        <Drawer opened={readerNotesOpen} onClose={() => setReaderNotesOpen(false)} position="right" size="md" title={<Text ff="var(--font-serif)" fw={500} fz="lg">Saved notes</Text>}>
          {pick(Boolean(notes.length === 0), () => (<Text c="dimmed" fz="sm" ta="center" py="xl">
              Nothing saved yet. Select a passage or use “Save” on an answer to keep it here.
            </Text>), () => (<Stack gap="sm">
              {notes.map((n) => (<Paper key={n.id} withBorder radius="md" p="sm">
                  {choose(Boolean(n.quote), (<Text fz="xs" c="dimmed" fs="italic" mb={6} lineClamp={4} style={{ borderLeft: "2px solid var(--mantine-color-lavender-4)", paddingLeft: 8 }}>
                      {n.quote}
                    </Text>), null)}
                  <Text fz="sm" lh={1.55} style={{ whiteSpace: "pre-wrap" }}>{n.content}</Text>
                  <Group justify="flex-end" mt={6}>
                    <Button size="compact-xs" variant="subtle" color="gray" onClick={() => void savedNotesActions.remove(n.id)}>
                      Delete
                    </Button>
                  </Group>
                </Paper>))}
            </Stack>))}
        </Drawer>
        </Box>
      </Box>);
        }), () => (<Box flex={1} mih={0} h="100%" mx={{ base: "calc(-1 * var(--mantine-spacing-xs))", sm: "calc(-1 * var(--mantine-spacing-md))" }} my={{ base: "calc(-1 * var(--mantine-spacing-xs))", sm: "calc(-1 * var(--mantine-spacing-md))" }} style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <Box flex={1} mih={0} h="100%" style={{ display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
      {studyMetaBar}
      {choose(Boolean(isLg), (<>
          <Box ref={studyRowRef} flex={1} mih={0} h="100%" pos="relative" style={{ display: "flex", overflow: "hidden", minHeight: 0 }}>
            {/* Base layer: the question column fills the row; the Source and Tutor
                    windows float above it so the learner can keep answering. */}
            <Box flex={1} mih={0} miw={0} pos="relative" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
              {questionColumn}
            </Box>

            {pick(Boolean(!isNewspaper), () => pick(Boolean(!sourceOpen), () => (<StudyEdgeTrigger side="left" icon={<IconFileText size={20} stroke={2}/>} label="Source" color="lavender" onClick={openSource}/>), () => !sourceOpen), () => !isNewspaper)}
            {pick(Boolean(mode !== "test"), () => pick(Boolean(!tutorOpen), () => (<StudyEdgeTrigger side="right" icon={<IconMessageCircle size={20} stroke={2}/>} label={ZIVO_ASSISTANT_NAME} color="sage" onClick={openTutor}/>), () => !tutorOpen), () => mode !== "test")}

            {choose(Boolean(!isNewspaper), (<FloatingPanel ref={sourcePanelRef} open={sourceOpen} title={shortFilename} icon={<IconFileText size={16} stroke={2}/>} accent="lavender" storageKey="zv-float-source" containerRef={studyRowRef} defaultSide="left" topInset={0} onClose={closeSource} onGeometryChange={setSourceGeometry}>
              <StudySourcePanel filename={artifact.filename} pageRange={selectedRange} currentPage={queue?.current_page} artifactId={artifact.id} isPdf={isPdf} pdfLoading={pdfLoading} pdfError={pdfError} pdfDoc={pdfDoc} studyPages={studyPages} pageTexts={pageTexts} pageTextsLoading={pageTextsLoading} open={sourceOpen}/>
            </FloatingPanel>), null)}

            {choose(Boolean(mode !== "test"), (<FloatingPanel ref={tutorPanelRef} open={tutorOpen} title={ZIVO_ASSISTANT_NAME} icon={<IconMessageCircle size={16} stroke={2}/>} accent="sage" storageKey="zv-float-tutor" containerRef={studyRowRef} defaultSide="right" topInset={0} onClose={closeTutor} onGeometryChange={setTutorGeometry}>
              <TutorPanel messages={chatMessages} input={chatInput} busy={chatBusy} contextReady={chatContextReady} onInputChange={setChatInput} onSend={() => void sendChat()} onStop={stopChat} onRegenerate={regenerateChat} onEditUser={editChatFromUser} onClear={() => void clearChat()} {...brainstormChatProps} emptyHint={choose(Boolean(mode === "brainstorm"), "Think out loud about this source. Every reply ends with three angles you could pull.", tutorNewspaperHint)}/>
            </FloatingPanel>), null)}
          </Box>
        </>), (<StudyMobileShell focusTutorKey={mobileTutorFocus} tutorHidden={mode === "test"} sourceHidden={isNewspaper} question={questionColumn} renderSource={(visible) => (<StudySourcePanel filename={artifact.filename} pageRange={selectedRange} currentPage={queue?.current_page} artifactId={artifact.id} isPdf={isPdf} pdfLoading={pdfLoading} pdfError={pdfError} pdfDoc={pdfDoc} studyPages={studyPages} pageTexts={pageTexts} pageTextsLoading={pageTextsLoading} open={visible}/>)} renderTutor={() => (<TutorPanel messages={chatMessages} input={chatInput} busy={chatBusy} contextReady={chatContextReady} onInputChange={setChatInput} onSend={() => void sendChat()} onStop={stopChat} onRegenerate={regenerateChat} onEditUser={editChatFromUser} onClear={() => void clearChat()} showHeader {...brainstormChatProps} emptyHint={choose(Boolean(mode === "brainstorm"), "Think out loud about this source. Every reply ends with three angles you could pull.", tutorNewspaperHint)}/>)}/>))}
      {pick(Boolean(reselectOpen), () => pick(Boolean(!isNewspaper), () => (<StudyRangeReselectOverlay filename={shortFilename} pageCount={pageCount} completedRange={completedRange} sliderFrom={sliderFrom} sliderTo={sliderTo} sliderMarks={sliderMarks} selectedPages={sortedSelection} isDark={isDark} isCompact={isNarrow} isPdf={isPdf} pdfDoc={pdfDoc} pageTexts={pageTexts} pageTextsLoading={pageTextsLoading} thumbCanvasRefs={thumbCanvasRefs} confirming={confirming} confirmingMode={confirmingMode} setupError={setupError} bookFinished={nextRangeSuggestion?.bookFinished ?? false} onRangeChange={handleRangeChange} onPageToggle={handlePageToggle} onSelectAll={() => {
                    setSelectedPages(pagesInRange(1, pageCount));
                    setLastClickedPage(1);
                }} onClearAll={() => {
                    setSelectedPages([]);
                    setLastClickedPage(null);
                }} onClose={() => setReselectOpen(false)} onConfirmNow={() => void confirmRange("now")}/>), () => !isNewspaper), () => reselectOpen)}
      </Box>
    </Box>));
    }))))));
}
