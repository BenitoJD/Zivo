"use client";

import { Modal } from "@mantine/core";
import { SourceImportDeck } from "@/app/workspace/_components/SourceImportDeck";

/** Sidebar "Add source" popup — the full import deck, reachable from anywhere. */
export function AddSourceModal({
  opened,
  onClose,
  onImported,
}: {
  opened: boolean;
  onClose: () => void;
  onImported: (id: string) => void;
}) {
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Add source"
      centered
      size="lg"
      radius="xl"
      styles={{ title: { fontWeight: 600, fontSize: "1.05rem" } }}
    >
      <SourceImportDeck
        onImported={(id) => {
          onClose();
          onImported(id);
        }}
      />
    </Modal>
  );
}
