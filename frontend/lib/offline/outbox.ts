/**
 * Outbox: offline grades queued for sync.
 *
 * Every grade captured offline appends here; on reconnect the sync layer drains
 * the batch to /api/mcq/grade/batch, where the server RECOMPUTES correctness
 * from the stored key (never trusting a client flag) and replays through the
 * real engines. Idempotent on the server via the measurement unique index.
 */
import { offlineDb, type OutboxGrade } from "./db";

export interface PendingGrade {
  assertionId: string;
  choiceIndex: number;
  choiceIndices: number[] | null;
  mode: string;
}

/** Queue one offline grade. Returns the created outbox row. */
export async function enqueueGrade(packId: string, grade: PendingGrade): Promise<OutboxGrade> {
  const db = offlineDb();
  const row: OutboxGrade = {
    uuid: crypto.randomUUID(),
    packId,
    assertionId: grade.assertionId,
    choiceIndex: grade.choiceIndex,
    choiceIndices: grade.choiceIndices,
    mode: grade.mode,
    createdAt: new Date().toISOString(),
    synced: false,
  };
  await db.outbox.put(row);
  return row;
}

/** All unsent grades for a pack (drained on sync). */
export async function pendingForPack(packId: string): Promise<OutboxGrade[]> {
  return offlineDb().outbox.where({ packId, synced: 0 }).toArray();
}

/** Count of unsent grades across all packs (for a global badge). */
export async function pendingCount(): Promise<number> {
  return offlineDb().outbox.where("synced").equals(0 as never).count();
}

/** Mark a batch drained, then remove them (keep the table small). */
export async function markSynced(uuids: string[]): Promise<void> {
  const db = offlineDb();
  await db.transaction("rw", db.outbox, async () => {
    for (const uuid of uuids) await db.outbox.delete(uuid);
  });
}
