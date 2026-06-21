"use client";

export type WorkspacePane = "mcq" | "source" | "chat";

const LABELS: Record<WorkspacePane, string> = {
  mcq: "MCQ",
  source: "Source",
  chat: "Chat",
};

export function WorkspaceTabBar({
  active,
  onChange,
}: {
  active: WorkspacePane;
  onChange: (p: WorkspacePane) => void;
}) {
  return (
    <nav className="workspace-tab-bar" aria-label="Workspace panels">
      {(Object.keys(LABELS) as WorkspacePane[]).map((key) => (
        <button
          key={key}
          type="button"
          className={`workspace-tab-bar__btn${active === key ? " is-active" : ""}`}
          onClick={() => onChange(key)}
        >
          {LABELS[key]}
        </button>
      ))}
    </nav>
  );
}
