/**
 * Pack prepare: create + poll + download + persist (the one moment that needs
 * network). After this, the learner studies with zero connectivity.
 */
import { apiGet, apiPost } from "@/lib/api/client";
import { offlineDb, type OfflineAssertion, type OfflinePack } from "./db";

/** Server pack metadata during build. */
interface PackStatus {
  id: string;
  document_id: string;
  status: "pending" | "building" | "ready" | "failed";
  progress: number;
  question_count: number;
  expires_at: string;
  error?: string | null;
}

/** The downloaded pack envelope (the one shape that carries answer keys). */
interface PackPayload {
  schema_version: number;
  document_id: string;
  artifact: Record<string, unknown>;
  serve_mode: string;
  deck: {
    id: string;
    title?: string | null;
    summary?: string | null;
    sequence: number;
    payload: Record<string, unknown>;
  }[];
  mastery: { answered_ids: string[]; current_page: number; budget_serve_mode: string };
  question_count: number;
  expires_at: string;
  signature?: string;
}

export type PrepareState =
  | { phase: "creating" }
  | { phase: "building"; progress: number }
  | { phase: "ready"; questionCount: number }
  | { phase: "failed"; error: string };

/** Create a pack and poll until ready/failed, then download + store it. */
export async function preparePack(
  documentId: string,
  onProgress?: (state: PrepareState) => void,
): Promise<OfflinePack> {
  onProgress?.({ phase: "creating" });
  const { id } = await apiPost<{ id: string; status: string }>("/api/offline/packs", {
    document_id: documentId,
  });

  // Poll build status. The ETA job backfills feedback across the deck, so this
  // can take a while for large sources; poll with a gentle backoff.
  const pack = await pollUntilReady(id, onProgress);
  const payload = await apiGet<PackPayload>(`/api/offline/packs/${id}/download`);

  return persistPack(payload, pack.expires_at);
}

async function pollUntilReady(
  packId: string,
  onProgress?: (state: PrepareState) => void,
): Promise<PackStatus> {
  const delays = [1500, 2000, 3000, 5000]; // backoff, then steady 5s
  let attempt = 0;
  while (true) {
    const status = await apiGet<PackStatus>(`/api/offline/packs/${packId}`);
    if (status.status === "ready") {
      onProgress?.({ phase: "ready", questionCount: status.question_count });
      return status;
    }
    if (status.status === "failed") {
      onProgress?.({ phase: "failed", error: status.error || "Build failed" });
      throw new Error(status.error || "Offline pack build failed");
    }
    onProgress?.({ phase: "building", progress: status.progress });
    const delay = delays[Math.min(attempt, delays.length - 1)];
    await sleep(delay);
    attempt += 1;
  }
}

/** Store the pack + its deck into IndexedDB, replacing any prior pack for it. */
export async function persistPack(payload: PackPayload, expiresAt: string): Promise<OfflinePack> {
  const db = offlineDb();
  const docId = payload.document_id;

  // Replace any existing pack for this source (one live pack per artifact).
  await db.transaction("rw", db.packs, db.assertions, async () => {
    const prior = await db.packs.where("documentId").equals(docId).toArray();
    for (const p of prior) {
      await db.assertions.where("packId").equals(p.id).delete();
      await db.packs.delete(p.id);
    }
  });

  const pack: OfflinePack = {
    id: crypto.randomUUID(),
    documentId: docId,
    expiresAt,
    signature: payload.signature ?? "",
    schemaVersion: payload.schema_version,
    artifact: payload.artifact,
    serveMode: payload.serve_mode,
    mastery: payload.mastery,
    questionCount: payload.question_count,
    downloadedAt: new Date().toISOString(),
  };
  const assertions: OfflineAssertion[] = payload.deck.map((d) => ({
    id: d.id,
    packId: pack.id,
    sequence: d.sequence,
    payload: d.payload,
    title: d.title,
    summary: d.summary,
  }));

  await db.transaction("rw", db.packs, db.assertions, async () => {
    await db.packs.put(pack);
    await db.assertions.bulkPut(assertions);
  });
  return pack;
}

/** Drop a pack + its deck + pending outbox entries (client-side revoke). */
export async function removePack(packId: string): Promise<void> {
  const db = offlineDb();
  await db.transaction("rw", db.packs, db.assertions, db.outbox, async () => {
    await db.assertions.where("packId").equals(packId).delete();
    await db.outbox.where("packId").equals(packId).delete();
    await db.packs.delete(packId);
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
