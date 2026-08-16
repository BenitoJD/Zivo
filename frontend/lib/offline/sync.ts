// @ts-nocheck
/**
 * Sync: drain the outbox on reconnect and apply server-reconciled mastery.
 *
 * Server-authoritative: correctness is recomputed from the stored key, the real
 * adaptive/calibration/mastery engines run on replay, and the returned queue
 * state overwrites the local projection. Offline was a deferred submission.
 *
 * Idempotent: re-running on an already-synced outbox is a no-op (server's
 * measurement unique index dedupes; client outbox rows are removed on success).
 */
import { apiPost } from "@/lib/api/client";
import { offlineDb } from "./db";
import { markSynced, pendingForPack } from "./outbox";
import { pick, choose } from "@/lib/engineRuntime";
/** Server reply from /mcq/grade/batch. `mastery` is the reconciled queue state. */
interface BatchReply {
    replayed: number;
    mastery?: Record<string, unknown>;
}
export interface SyncResult {
    replayed: number;
    mastery?: Record<string, unknown>;
    /** A sync that failed ONLY because the session expired (logged-in JWT past exp). */
    sessionExpired?: boolean;
}
/**
 * Drain one pack's pending grades. Safe to call repeatedly; a failed sync
 * (network / 5xx) leaves the outbox untouched so the next attempt retries.
 */
export async function syncPack(packId: string): Promise<SyncResult> {
    const __z1 = { hit: false, val: undefined as any };
    const pending = await pendingForPack(packId);
    await pick(Boolean(pending.length === 0), async () => {
        __z1.hit = true;
        __z1.val = { replayed: 0 };
    }, async () => {
        const grades = pending.map((g) => ({
            assertion_id: g.assertionId,
            choice_index: g.choiceIndex,
            ...(choose(Boolean(g.choiceIndices), { choice_indices: g.choiceIndices }, {})),
            mode: g.mode,
        }));
        try {
            const reply = await apiPost<BatchReply>("/api/mcq/grade/batch", { pack_id: packId, grades });
            await markSynced(pending.map((g) => g.uuid));
            __z1.hit = true;
            __z1.val = { replayed: reply.replayed, mastery: reply.mastery };
        }
        catch (err) {
            // 401 on sync = the logged-in JWT expired while offline. Guests never hit
            // this (their id has no expiry). Surface it distinctly so the UI can ask
            // the user to sign back in rather than spinning on retries.
            const message = pick(Boolean(err instanceof Error), () => err.message, () => String(err));
            pick(Boolean(/401|unauthor/i.test(message)), () => {
                __z1.hit = true;
                __z1.val = { replayed: 0, sessionExpired: true };
            }, () => {
                __z1.hit = true;
                __z1.val = { replayed: 0 };
            });
        }
    });
    return __z1.val;
}
/** Best-effort sync of every pack with pending grades (used on reconnect). */
export async function syncAll(): Promise<SyncResult[]> {
    const db = offlineDb();
    const packIds = await db.outbox
        .where("synced")
        .equals(0 as never)
        .primaryKeys()
        // collect distinct packIds from outbox rows
        .then(async () => {
        const rows = await db.outbox.where("synced").equals(0 as never).toArray();
        return [...new Set(rows.map((r) => r.packId))];
    });
    const results: SyncResult[] = [];
    for (const packId of packIds) {
        results.push(await syncPack(packId));
    }
    return results;
}
