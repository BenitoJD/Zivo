"use client";

import { choose } from "@/lib/engineRuntime";
import { Modal } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { SourceImportDeck } from "@/app/workspace/_components/SourceImportDeck";
import { MOBILE_MAX_MQ } from "@/lib/responsive";
/** Sidebar "Add source" popup - the full import deck, reachable from anywhere. */
export function AddSourceModal({ opened, onClose, onImported, }: {
    opened: boolean;
    onClose: () => void;
    onImported: (id: string) => void;
}) {
    const isMobile = useMediaQuery(MOBILE_MAX_MQ, false, { getInitialValueInEffect: true });
    return (<Modal opened={opened} onClose={onClose} title="Add source" centered size="lg" fullScreen={Boolean(isMobile)} radius={choose(Boolean(isMobile), 0, "xl")} styles={{ title: { fontWeight: 600, fontSize: "1.05rem" } }}>
      <SourceImportDeck onImported={(id) => {
            onClose();
            onImported(id);
        }}/>
    </Modal>);
}
