/** Quiz share client — types and API calls for shared MCQ quiz sets. */

import { apiGet, apiPost } from "@/lib/api/client";

export type QuizQuestion = {
    id: string;
    question_text: string;
    options: string[];
    sort_order: number;
    explanation?: string;
};

export type QuizAttemptSummary = {
    taker_name: string;
    score: number;
    total_questions: number;
    completed_at: string;
};

export type QuizSet = {
    id: string;
    title: string;
    description: string;
    creator: string;
    slug: string;
    created_at: string;
    questions: QuizQuestion[];
    attempts: QuizAttemptSummary[];
};

export type QuizVerdict = {
    question_id: string;
    selected: number;
    correct_index: number;
    is_correct: boolean;
};

export type QuizAttemptResult = {
    score: number;
    total: number;
    results: QuizVerdict[];
};

export type CreatedQuiz = {
    id: string;
    slug: string;
    title: string;
    question_count: number;
};

export type CreatorResultRow = QuizAttemptSummary & {
    answers: QuizVerdict[];
};

export type DraftQuestion = {
    question_text: string;
    options: string[];
    correct_index: number;
    explanation: string;
};

export async function createQuiz(body: {
    title: string;
    description: string;
    creator_name: string;
    questions: DraftQuestion[];
}): Promise<CreatedQuiz> {
    return apiPost<CreatedQuiz>("/api/quiz/sets", body);
}

export async function fetchQuiz(slug: string): Promise<QuizSet> {
    return apiGet<QuizSet>(`/api/quiz/sets/${slug}`);
}

export async function fetchQuizManage(slug: string): Promise<QuizSet & { results: CreatorResultRow[] }> {
    return apiGet<QuizSet & { results: CreatorResultRow[] }>(`/api/quiz/sets/${slug}/manage`);
}

export async function submitQuizAttempt(
    slug: string,
    body: { taker_name: string; answers: number[] },
): Promise<QuizAttemptResult> {
    return apiPost<QuizAttemptResult>(`/api/quiz/sets/${slug}/submit`, body);
}

export async function generateQuizQuestions(body: {
    topic: string;
    count: number;
    difficulty: string;
}): Promise<{ questions: DraftQuestion[] }> {
    return apiPost<{ questions: DraftQuestion[] }>("/api/quiz/generate", body);
}

export async function fixQuizQuestions(questions: DraftQuestion[]): Promise<{ questions: DraftQuestion[] }> {
    return apiPost<{ questions: DraftQuestion[] }>("/api/quiz/fix", { questions });
}

/** Deterministic answer letter for MCQ display. */
export function answerLabel(index: number): string {
    return "ABCDEFGH"[index] ?? String(index + 1);
}
