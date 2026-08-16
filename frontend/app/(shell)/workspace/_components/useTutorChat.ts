// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { chatSurfaceForMode, queryKeys, useChatMessagesQuery, useSavedNotesActions, } from "@/lib/api/queries";
import { apiGet, apiPost, apiPostSSE, ensureGuestSession, humanizeApiFailure } from "@/lib/api/client";
import { isTransientChatAssistantMessage } from "@/lib/constants";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";
import type { McqState } from "@/lib/types";
import type { StudyMode } from "@/app/workspace/_components/studyNav";
export type ChatCitation = {
    document_id: string;
    chunk_id?: string | null;
    page_start?: number | null;
    page_end?: number | null;
    score?: number | null;
    snippet?: string;
};
export type ChatMessage = {
    role: string;
    content: string;
    citations?: ChatCitation[] | null;
};
type WikipediaLookup = {
    title: string;
    extract: string;
    source_url: string;
};
const WIKI_EXTRACT_MAX = 480;
/**
 * Tutor-chat surface, extracted from the workspace page orchestrator.
 *
 * Owns the per-mode conversation: hydration from the server thread, the streaming
 * assistant (send / regenerate / edit-and-resend / stop), Read-mode Study-Buddy
 * helpers (quote a passage, ask directly, save a note), and "clear chat". Returns
 * the same names the page already used so the render is unchanged.
 *
 * The scope sent with each message is mode-aware: Read talks about the document
 * being read (never the Learn queue's page), while Learn/Test pin the current
 * page, question, and the learner's current/confirmed choice.
 */
export function useTutorChat({ artifactId, mode, enabled, queue, currentAssertionId, questionPage, selected, multiSelected, isMulti, gradeState, savedNotesActions, onSavedNote, }: {
    artifactId: string;
    mode: StudyMode;
    enabled: boolean;
    queue: McqState | null;
    /** The question on screen (may differ from queue while feedback is pinned). */
    currentAssertionId?: string | null;
    /** Source page for the active question (newspaper MCQs). */
    questionPage?: number | null;
    selected: string | null;
    multiSelected?: number[];
    isMulti?: boolean;
    gradeState: {
        correct: boolean;
        correctIndex: number;
        correctIndices?: number[];
    } | null;
    savedNotesActions: ReturnType<typeof useSavedNotesActions>;
    onSavedNote: () => void;
}) {
    const queryClient = useQueryClient();
    const chatSurface = chatSurfaceForMode(mode);
    const chatMessagesQuery = useChatMessagesQuery(artifactId, mode, enabled);
    const [chatInput, setChatInput] = useState("");
    const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
    const [chatBusy, setChatBusy] = useState(false);
    const chatAbortRef = useRef<AbortController | null>(null);
    const chatHydratedRef = useRef<string | null>(null);
    const chatContextReady = queue?.rag_window_ready !== false;
    // Switching conversation surface (mode) clears the view until the new thread loads.
    // Also abort any in-flight stream: otherwise chatBusy stays true and Read/Learn
    // Send silently no-ops after a mid-stream hop.
    useEffect(() => {
        chatAbortRef.current?.abort();
        chatAbortRef.current = null;
        // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrate chat thread from server once per surface (chat is appended to locally during streaming)
        setChatBusy(false);
        chatHydratedRef.current = null;
        setChatMessages([]);
    }, [artifactId, chatSurface]);
    useEffect(() => {
        return pick(Boolean(!chatMessagesQuery.data || chatBusy), () => {
            return;
        }, () => {
            const key = `${artifactId}:${chatSurface}`;
            return pick(Boolean(chatHydratedRef.current === key), () => {
                return;
            }, () => {
                chatHydratedRef.current = key;
                setChatMessages(chatMessagesQuery.data
                    .filter((m) => (m.content || "").trim())
                    .filter((m) => !(pick(Boolean(m.role === "assistant"), () => isTransientChatAssistantMessage(m.content), () => m.role === "assistant")))
                    .map((m) => ({
                    role: m.role,
                    content: m.content,
                    citations: (m as {
                        citations?: ChatCitation[] | null;
                    }).citations ?? null,
                })));
            });
        });
    }, [artifactId, chatSurface, chatBusy, chatMessagesQuery.data]);
    // Abort any in-flight stream when the page unmounts.
    useEffect(() => {
        return () => {
            chatAbortRef.current?.abort();
        };
    }, []);
    // Core streamer - assumes chatMessages already ends with the user turn + an empty
    // assistant placeholder to fill. Shared by send, regenerate, and edit-and-resend.
    async function runAssistant(userMsg: string, extraScope?: Record<string, unknown>) {
        const __z1 = { hit: false, val: undefined as any };
        setChatBusy(true);
        chatAbortRef.current?.abort();
        const abort = new AbortController();
        chatAbortRef.current = abort;
        try {
            await ensureGuestSession();
            // Scope the chat to the CURRENT mode. In Read mode the buddy is about the
            // document the user is reading - never the Learn queue's current page - so
            // it must not pin the learn page/question.
            const scope: Record<string, unknown> = { mode, ...extraScope };
            pick(Boolean(mode !== "read"), () => {
                const assertionId = currentAssertionId ?? queue?.current_assertion_id;
                pick(Boolean(assertionId), () => {
                    scope.current_assertion_id = assertionId;
                }, () => {
                });
                const page = questionPage ?? queue?.current_page;
                pick(Boolean(page && page > 0), () => {
                    scope.current_page = page;
                }, () => {
                });
                pick(Boolean(isMulti && (multiSelected?.length ?? 0) > 0), () => {
                    scope.selected_choice_indices = multiSelected;
                    scope.selected_choice_index = multiSelected![0];
                }, () => {
                    pick(Boolean(selected !== null), () => {
                        scope.selected_choice_index = Number(selected);
                    }, () => {
                    });
                });
                pick(Boolean(gradeState !== null), () => {/*..............................................................................*/
                    pick(Boolean(isMulti && (multiSelected?.length ?? 0) > 0), () => {
                        scope.confirmed_choice_indices = multiSelected;
                        scope.confirmed_choice_index = multiSelected![0];
                    }, () => {
                        pick(Boolean(selected !== null), () => {
                            scope.confirmed_choice_index = Number(selected);
                        }, () => {
                        });
                    });
                    scope.answer_correct = gradeState.correct;
                }, () => {
                });
            }, () => {
            });
            let assistant = "";
            let gotToken = false;
            let citations: ChatCitation[] | null = null;
            await apiPostSSE("/api/chat", { document_id: artifactId, message: userMsg, scope }, {
                onChunk: (chunk) => {
                    gotToken = true;
                    assistant += chunk;
                    setChatMessages((m) => {
                        const copy = [...m];
                        const i = copy.length - 1;
                        const last = copy[i];
                        pick(Boolean(last?.role === "assistant"), () => {
                            copy[i] = { ...last, content: assistant, citations: citations ?? last.citations };
                        }, () => {
                            copy.push({ role: "assistant", content: assistant, citations });
                        });
                        return copy;
                    });
                },
                onEvent: (event, data) => {
                    pick(Boolean(event === "sources"), () => {
                        try {
                            const payload = JSON.parse(data) as {
                                citations?: ChatCitation[];
                            };
                            pick(Boolean(Array.isArray(payload.citations) && payload.citations.length > 0), () => {
                                citations = payload.citations;
                                setChatMessages((m) => {
                                    const copy = [...m];
                                    const i = copy.length - 1;
                                    const last = copy[i];
                                    pick(Boolean(last?.role === "assistant"), () => {
                                        copy[i] = { ...last, citations };
                                    }, () => {
                                    });
                                    return copy;
                                });
                            }, () => {
                            });
                        }
                        catch {
                        }
                    }, () => {
                    });
                },
            }, { signal: abort.signal });
            pick(Boolean(!gotToken || !assistant.trim()), () => {
                throw new Error("empty response");
            }, () => {
            });
        }
        catch (e) {/*..............................................................................*/
            pick(Boolean(e instanceof DOMException && e.name === "AbortError"), () => {
                setChatMessages((m) => {
                    const copy = [...m];
                    pick(Boolean(copy[copy.length - 1]?.role === "assistant" && !copy[copy.length - 1]?.content), () => {
                        copy.pop();
                    }, () => {
                    });
                    return copy;
                });
                __z1.hit = true;
            }, () => {/*..............................................................................*/
                const raw = choose(Boolean(e instanceof Error && e.message), e.message, null);
                const timedOut = pick(Boolean(raw), () => /timed out|aborted/i.test(raw), () => raw);
                const detail = pick(Boolean(timedOut), () => `${ZIVO_ASSISTANT_NAME} is still waking up - try again in a moment.`, () => pick(Boolean(raw && raw !== "empty response"), () => humanizeApiFailure(0, raw), () => `${ZIVO_ASSISTANT_NAME} could not reply right now. Try again in a moment.`));
                setChatMessages((m) => {
                    const copy = [...m];
                    const i = copy.length - 1;
                    const last = copy[i];
                    pick(Boolean(last?.role === "assistant"), () => {
                        copy[i] = { ...last, content: detail };
                    }, () => {
                        copy.push({ role: "assistant", content: detail });
                    });
                    return copy;
                });
            });
        }
        finally {
            pick(Boolean(chatAbortRef.current === abort), () => {
                chatAbortRef.current = null;
            }, () => {
            });
            setChatBusy(false);
        }
    }
    function sendChat() {
        return pick(Boolean(!chatInput.trim() || chatBusy || !chatContextReady), () => {
            return;
        }, () => {
            const userMsg = chatInput.trim();
            setChatInput("");
            setChatMessages((m) => [...m, { role: "user", content: userMsg }, { role: "assistant", content: "" }]);
            void runAssistant(userMsg);
        });
    }
    // Clear the current conversation: empty the panel now, start a fresh thread on the
    // server (per-surface), and drop the cached messages so it stays empty.
    async function clearChat() {
        return await pick(Boolean(chatBusy), async () => {
            return;
        }, async () => {
            setChatMessages([]);
            setChatInput("");
            chatHydratedRef.current = `${artifactId}:${chatSurface}`;
            try {
                await apiPost(`/api/chat/threads/${artifactId}/clear?surface=${chatSurface}`, {});
            }
            catch {
            }
            void queryClient.invalidateQueries({ queryKey: queryKeys.chatMessages(artifactId, chatSurface) });
        });
    }
    // Read-mode (Study Buddy) helpers.
    function quoteToComposer(text: string) {
        const t = text.trim().replace(/\s+/g, " ");
        return pick(Boolean(!t), () => {
            return;
        }, () => {
            setChatInput(`About this passage:\n"${t}"\n\nMy question: `);
        });
    }
    function askBuddy(message: string, extraScope?: Record<string, unknown>) {
        return pick(Boolean(chatBusy || !chatContextReady), () => {
            return;
        }, () => {
            const msg = message.trim();
            return pick(Boolean(!msg), () => {
                return;
            }, () => {
                setChatMessages((m) => [...m, { role: "user", content: msg }, { role: "assistant", content: "" }]);
                void runAssistant(msg, extraScope);
            });
        });
    }
    /** Wikipedia selection: show the turn immediately, fetch the summary, then stream. */
    async function askBuddyWithWikipedia(selection: string) {
        return await pick(Boolean(chatBusy || !chatContextReady), async () => {
            return;
        }, async () => {
            const text = selection.trim();
            return await pick(Boolean(!text), async () => {
                return;
            }, async () => {
                const token = text.split(/\s+/)[0] || text;
                setChatMessages((m) => [
                    ...m,
                    { role: "user", content: `Explain "${text}" — looking up Wikipedia…` },
                    { role: "assistant", content: "" },
                ]);
                setChatBusy(true);
                let userMsg: string;
                try {
                    const wiki = await apiGet<WikipediaLookup>(`/api/reference/wikipedia?query=${encodeURIComponent(token)}`);
                    const extract = pick(Boolean(wiki.extract.length > WIKI_EXTRACT_MAX), () => `${wiki.extract.slice(0, WIKI_EXTRACT_MAX).trimEnd()}…`, () => wiki.extract);
                    userMsg =
                        `I highlighted this while studying:\n\n"${text}"\n\n` +
                            `Wikipedia summary of "${wiki.title}":\n${extract}\n\n` +
                            `Explain what this means in the context of what I'm learning. Keep it plain and short.`;
                }
                catch {
                    userMsg =
                        `I highlighted: "${text}"\n\n` +
                            `Use Wikipedia as your source and explain what this means in plain language.`;
                }
                setChatMessages((m) => {
                    const copy = [...m];
                    const userIdx = copy.length - 2;
                    pick(Boolean(copy[userIdx]?.role === "user"), () => {
                        copy[userIdx] = { role: "user", content: userMsg };
                    }, () => {
                    });
                    return copy;
                });
                await runAssistant(userMsg, { reference_source: "wikipedia" });
            });
        });
    }
    function saveNote(content: string, quote?: string | null) {
        const c = content.trim();
        return pick(Boolean(!c), () => {
            return;
        }, () => {
            void savedNotesActions.save(c, quote ?? null);
            onSavedNote();
        });
    }
    // Regenerate the most recent answer: drop the trailing assistant turn and re-ask.
    function regenerateChat() {
        return pick(Boolean(chatBusy || !chatContextReady), () => {
            return;
        }, () => {
            const lastUserIdx = chatMessages.map((x) => x.role).lastIndexOf("user");
            return pick(Boolean(lastUserIdx === -1), () => {
                return;
            }, () => {
                const lastUser = chatMessages[lastUserIdx].content;
                const extraScope = choose(Boolean(lastUser.includes("Wikipedia summary of")), { reference_source: "wikipedia" }, undefined);
                setChatMessages([...chatMessages.slice(0, lastUserIdx + 1), { role: "assistant", content: "" }]);
                void runAssistant(lastUser, extraScope);
            });
        });
    }
    // Edit a previous question: lift it into the composer and trim the thread from there.
    function editChatFromUser(index: number) {
        return pick(Boolean(chatBusy), () => {
            return;
        }, () => {
            const msg = chatMessages[index];
            return pick(Boolean(!msg || msg.role !== "user"), () => {
                return;
            }, () => {
                setChatInput(msg.content);
                setChatMessages(chatMessages.slice(0, index));
            });
        });
    }
    function stopChat() {
        chatAbortRef.current?.abort();
    }
    // "Add to Learn": persist chat-produced MCQs (zv-mcq blocks) into the pool.
    const [mcqPersistState, setMcqPersistState] = useState<{
        busy: boolean;
        done: number | null;
        error: string | null;
    }>({ busy: false, done: null, error: null });
    async function addChatMcqsToLearn(questions: {
        question: string;
        options: string[];
        correct_index: number;
        explanation?: string;
    }[]) {
        return await pick(Boolean(!questions.length || mcqPersistState.busy), async () => {
            return;
        }, async () => {
            setMcqPersistState({ busy: true, done: null, error: null });
            try {
                await ensureGuestSession();
                const res = await apiPost<{
                    persisted: number;
                }>(`/api/chat/mcqs/persist?document_id=${artifactId}`, {
                    questions,
                    page_number: 1,
                    surface: chatSurface,
                });
                setMcqPersistState({ busy: false, done: res.persisted, error: null });
            }
            catch (e) {
                setMcqPersistState({
                    busy: false,
                    done: null,
                    error: choose(Boolean(e instanceof Error), e.message, "Could not add questions"),
                });
            }
        });
    }
    return {
        chatInput,
        setChatInput,
        chatMessages,
        chatBusy,
        chatContextReady,
        sendChat,
        clearChat,
        quoteToComposer,
        askBuddy,
        askBuddyWithWikipedia,
        saveNote,
        regenerateChat,
        editChatFromUser,
        stopChat,
        addChatMcqsToLearn,
        mcqPersistState,
    };
}
