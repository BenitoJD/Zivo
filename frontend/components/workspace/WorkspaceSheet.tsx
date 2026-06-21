"use client";

import type { ReactNode } from "react";

export function WorkspaceSheet({
  children,
  onClose,
}: {
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="workspace-sheet" role="dialog" aria-modal="true">
      <button type="button" className="workspace-sheet__close" onClick={onClose}>
        Back to MCQ
      </button>
      <div className="workspace-sheet__body">{children}</div>
    </div>
  );
}
