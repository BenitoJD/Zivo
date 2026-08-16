// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { Button, Group, Modal, Stack, Text } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { apiDelete, ensureGuestSession } from "@/lib/api/client";
import { MOBILE_MAX_MQ } from "@/lib/responsive";
import type { SourceDocument } from "@/lib/types";
const MODAL_OVERLAY_PROPS = { backgroundOpacity: 0.45, blur: 8 } as const;
/** Confirm-deletion modal for a source. Cleans up navigation if needed. */
export function DeleteSourceModal({ target, onClose, label, documents, onDeleted, }: {
    target: SourceDocument | null;
    onClose: () => void;
    /** Display label for the source filename (extension stripped). */
    label: (filename: string) => string;
    /** Current source list (used to decide where to navigate after delete). */
    documents: SourceDocument[];
    /** Refresh the source list after a successful delete. */
    onDeleted: () => Promise<void> | void;
}) {
    const router = useRouter();
    const pathname = usePathname();
    const isMobile = useMediaQuery(MOBILE_MAX_MQ, false, { getInitialValueInEffect: true });
    const [deleteBusy, setDeleteBusy] = useState(false);
    // Freeze display while deleteBusy so parent can clear `target` without blanking
    // the dialog mid-request / mid-close.
    const [frozenLabel, setFrozenLabel] = useState("");
    const artifactId = pick(Boolean(pathname.startsWith("/workspace/")), () => pathname.split("/")[2], () => undefined);
    const opened = Boolean(target) || deleteBusy;
    const displayName = pick(Boolean(target), () => label(target.filename), () => frozenLabel);
    async function confirmDeleteSource() {
        return await pick(Boolean(!target), async () => {
            return;
        }, async () => {
            const t = target;
            setFrozenLabel(label(t.filename));
            setDeleteBusy(true);
            try {
                await ensureGuestSession();
                await apiDelete(`/api/sources/${t.id}`);
                const remaining = documents.filter((d) => d.id !== t.id);
                onClose();
                pick(Boolean(artifactId === t.id), () => {
                    router.push(choose(Boolean(remaining[0]), `/workspace/${remaining[0].id}`, "/workspace"));
                }, () => {
                });
                await onDeleted();
                notifications.show({
                    title: "Source deleted",
                    message: `${label(t.filename)} was removed permanently.`,
                    color: "sage",
                });
            }
            catch (e) {
                notifications.show({
                    title: "Could not delete source",
                    message: choose(Boolean(e instanceof Error), e.message, "Unknown error"),
                    color: "terracotta",
                });
            }
            finally {
                setDeleteBusy(false);
                setFrozenLabel("");
            }
        });
    }
    return (<Modal opened={opened} onClose={() => {
            return pick(Boolean(deleteBusy), () => {
                return;
            }, () => {
                onClose();
            });
        }} title="Delete source?" centered fullScreen={Boolean(isMobile)} radius={choose(Boolean(isMobile), 0, "xl")} size={choose(Boolean(isMobile), undefined, "md")} overlayProps={MODAL_OVERLAY_PROPS} closeOnClickOutside={!deleteBusy} closeOnEscape={!deleteBusy}>
      <Stack gap="md">
        <Text size="sm" lh={1.55}>
          Permanently delete{" "}
          <Text span fw={600}>
            {displayName}
          </Text>
          ? This removes the file, generated questions, chat history, and cached responses. This
          cannot be undone.
        </Text>
        <Group justify="flex-end" gap="sm">
          <Button variant="default" onClick={onClose} disabled={deleteBusy}>
            Cancel
          </Button>
          <Button color="terracotta" loading={deleteBusy} onClick={() => void confirmDeleteSource()}>
            Delete permanently
          </Button>
        </Group>
      </Stack>
    </Modal>);
}
