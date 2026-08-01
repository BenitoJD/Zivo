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
  const pending = await pendingForPack(packId);
  if (pending.length === 0) return { replayed: 0 };

  const grades = pending.map((g) => ({
    assertion_id: g.assertionId,
    choice_index: g.choiceIndex,
    ...(g.choiceIndices ? { choice_indices: g.choiceIndices } : {}),
    mode: g.mode,
  }));

  try {
    const reply = await apiPost<BatchReply>("/api/mcq/grade/batch", { pack_id: packId, grades });
    await markSynced(pending.map((g) => g.uuid));
    return { replayed: reply.replayed, mastery: reply.mastery };
  } catch (err) {
    // 401 on sync = the logged-in JWT expired while offline. Guests never hit
    // this (their id has no expiry). Surface it distinctly so the UI can ask
    // the user to sign back in rather than spinning on retries.
    const message = err instanceof Error ? err.message : String(err);
    if (/401|unauthor/i.test(message)) {
      return { replayed: 0, sessionExpired: true };
    }
    // Transient (network / 5xx / 410-expired): leave outbox for the next attempt.
    return { replayed: 0 };
  }
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
  for (const packId of packIds) results.push(await syncPack(packId));
  return results;
}
