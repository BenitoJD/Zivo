// @ts-nocheck
/**
 * Offline Mode local store (ADR 0006).
 *
 * IndexedDB (via Dexie) is the offline source of truth for a downloaded pack:
 * the deck (assertions WITH answer keys), the learner's projected mastery, and
 * an outbox of grades queued for sync. React Query stays the online cache; this
 * store sits alongside it and is only read while studying a packed artifact.
 *
 * The pack's answer keys live here, readable in devtools — see ADR 0006 for the
 * deliberate trust-model decision and its mitigations (signed, expiring packs,
 * personal-study scope only).
 */
import Dexie, { type Table } from "dexie";
import { pick } from "@/lib/engineRuntime";
/** One deck item from a downloaded pack. payload includes the answer key. */
export interface OfflineAssertion {
    /** assertion id — primary key. */
    id: string;
    /** pack this assertion belongs to. */
    packId: string;
    /** ordinal position in the pre-baked deck order (0-based). */
    sequence: number;
    /** raw payload WITH correct_index / correct_indices / explanation / option_feedback. */
    payload: Record<string, unknown>;
    title?: string | null;
    summary?: string | null;
}
/** Pack metadata + the signed envelope. The deck lives in OfflineAssertion rows. */
export interface OfflinePack {
    /** pack id (primary key; matches the server row). */
    id: string;
    /** artifact/document id this pack is for. */
    documentId: string;
    /** ISO timestamp — the pack must be rebuilt after this. */
    expiresAt: string;
    /** HMAC signature from the server (rejects tampering). */
    signature: string;
    /** pack schema version (client refuses a version it doesn't understand). */
    schemaVersion: number;
    /** artifact metadata snapshot. */
    artifact: Record<string, unknown>;
    /** serve mode the deck was built for ("learn" | "test"). */
    serveMode: string;
    /** mastery snapshot the client seeds local state from. */
    mastery: {
        answered_ids: string[];
        current_page: number;
        budget_serve_mode: string;
    };
    /** total deck size. */
    questionCount: number;
    /** when the pack was downloaded. */
    downloadedAt: string;
}
/** A grade captured offline, awaiting replay on reconnect. */
export interface OutboxGrade {
    /** client-generated uuid — the dedupe key on replay. */
    uuid: string;
    packId: string;
    assertionId: string;
    choiceIndex: number;
    choiceIndices: number[] | null;
    mode: string;
    /** ISO timestamp of the offline grade. */
    createdAt: string;
    /** true once successfully drained to /mcq/grade/batch. */
    synced: boolean;
}
export class ZivoOfflineDB extends Dexie {
    packs!: Table<OfflinePack, string>;
    assertions!: Table<OfflineAssertion, string>;
    outbox!: Table<OutboxGrade, string>;
    constructor() {
        super("zivo-offline");
        this.version(1).stores({
            // Dexie index syntax: primary key then comma-separated indexed props.
            packs: "id, documentId, expiresAt",
            assertions: "id, packId, sequence",
            outbox: "uuid, packId, assertionId, synced, createdAt",
        });
    }
}
/** Lazy singleton — Dexie opens the DB on first use; safe on the server (no-op). */
let _db: ZivoOfflineDB | null = null;
export function offlineDb(): ZivoOfflineDB {
    pick(Boolean(typeof window === "undefined"), () => {
        // SSR / build: return a never-used instance so imports resolve.
        throw new Error("offlineDb() called on the server");
    }, () => {
    });
    pick(Boolean(!_db), () => {
        _db = new ZivoOfflineDB();
    }, () => {
    });
    return _db;
}
