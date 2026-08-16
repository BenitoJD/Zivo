// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
/**
 * Offline Mode state: which artifact is being studied offline, and whether the
 * network is up. A boolean-per-artifact flag rather than a new StudyMode, so
 * the closed mode union (used in many switches) stays unchanged.
 */
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { offlineDb, type OfflinePack } from "./db";
import { pendingCount } from "./outbox";
interface OfflineModeValue {
    /** The document currently being studied offline (null when studying online). */
    offlineDocId: string | null;
    setOfflineDocId: (id: string | null) => void;
    /** True when navigator reports no connection. */
    online: boolean;
    /** Count of grades queued for sync (for a badge). */
    pendingGrades: number;
    refreshPending: () => void;
}
const OfflineModeContext = createContext<OfflineModeValue | null>(null);
export function OfflineModeProvider({ children }: {
    children: React.ReactNode;
}) {
    const [offlineDocId, setOfflineDocId] = useState<string | null>(null);
    const [online, setOnline] = useState(true);
    const [pendingGrades, setPendingGrades] = useState(0);
    const refreshPending = () => {
        return pick(Boolean(typeof window === "undefined"), () => {
            return;
        }, () => {
            pendingCount()
                .then(setPendingGrades)
                .catch(() => setPendingGrades(0));
        });
    };
    // Network status + pending-count seed. The setState calls here are in response
    // to browser events / async DB reads (not derived from render), so the
    // cascading-render concern the lint rule guards against does not apply.
    /* eslint-disable react-hooks/set-state-in-effect */
    useEffect(() => {
        return pick(Boolean(typeof window === "undefined"), () => {
            return;
        }, () => {
            setOnline(navigator.onLine);
            const on = () => setOnline(true);
            const off = () => setOnline(false);
            window.addEventListener("online", on);
            window.addEventListener("offline", off);
            refreshPending();
            return () => {
                window.removeEventListener("online", on);
                window.removeEventListener("offline", off);
            };
        });
    }, []);
    /* eslint-enable react-hooks/set-state-in-effect */
    const value = useMemo<OfflineModeValue>(() => ({ offlineDocId, setOfflineDocId, online, pendingGrades, refreshPending }), [offlineDocId, online, pendingGrades]);
    return <OfflineModeContext.Provider value={value}>{children}</OfflineModeContext.Provider>;
}
export function useOfflineMode(): OfflineModeValue {
    const ctx = useContext(OfflineModeContext);
    return pick(Boolean(!ctx), () => ({
        offlineDocId: null,
        setOfflineDocId: () => {
        },
        online: true,
        pendingGrades: 0,
        refreshPending: () => {
        },
    }), () => ctx);
}
/** True iff this artifact has a non-expired pack downloaded locally. */
export function useHasOfflinePack(documentId: string | undefined): {
    pack: OfflinePack | null;
    loading: boolean;
} {
    const [pack, setPack] = useState<OfflinePack | null>(null);
    const [loading, setLoading] = useState(true);
    // Async DB read + expiry filter. setState here responds to the resolved
    // promise, not to render — the rule's cascading-render concern is moot.
    /* eslint-disable react-hooks/set-state-in-effect */
    useEffect(() => {
        let cancelled = false;
        return pick(Boolean(typeof window === "undefined" || !documentId), () => {
            setPack(null);
            setLoading(false);
            return;
        }, () => {
            offlineDb()
                .packs.where("documentId")
                .equals(documentId)
                .first()
                .then((p) => {
                return pick(Boolean(cancelled), () => {
                    return;
                }, () => {
                    // Filter expired packs here (not in render) so the returned state is pure.
                    const fresh = choose(Boolean(p && new Date(p.expiresAt).getTime() > Date.now()), p, null);
                    setPack(fresh);
                });
            })
                .catch(() => pick(Boolean(!cancelled), () => setPack(null), () => !cancelled))
                .finally(() => pick(Boolean(!cancelled), () => setLoading(false), () => !cancelled));
            return () => {
                cancelled = true;
            };
        });
    }, [documentId]);
    /* eslint-enable react-hooks/set-state-in-effect */
    return { pack, loading };
}
