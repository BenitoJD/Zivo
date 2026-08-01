/**
 * Pack reader: load the deck + pack metadata for the offline workspace branch.
 */
import type { QueryClient } from "@tanstack/react-query";
import { offlineDb, type OfflineAssertion, type OfflinePack } from "./db";

/** Load a pack (with its deck) for a document, or null if none/not-ready. */
export async function loadPackForDocument(
  documentId: string,
): Promise<{ pack: OfflinePack; deck: OfflineAssertion[] } | null> {
  const db = offlineDb();
  const pack = await db.packs.where("documentId").equals(documentId).first();
  if (!pack) return null;
  if (new Date(pack.expiresAt).getTime() < Date.now()) return null; // expired
  const deck = await db.assertions.where("packId").equals(pack.id).toArray();
  return { pack, deck };
}

/** Seed the React Query assertion cache for every deck item so useAssertionQuery
 *  returns instantly with no fetch while offline. The caller passes the queryClient
 *  + a key builder matching lib/api/queries.ts queryKeys.assertion. */
export function seedAssertionCache(
  deck: OfflineAssertion[],
  queryClient: QueryClient,
  assertionKey: (id: string) => readonly unknown[],
): void {
  for (const a of deck) {
    // Shape mirrors GET /api/assertions/{id} → { payload, title?, summary? }.
    queryClient.setQueryData(assertionKey(a.id), {
      payload: a.payload,
      title: a.title ?? undefined,
      summary: a.summary ?? undefined,
    });
  }
}
