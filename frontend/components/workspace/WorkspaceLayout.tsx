"use client";

import { useState } from "react";
import { McqPanel } from "@/components/workspace/McqPanel";
import { WorkspaceTabBar, type WorkspacePane } from "@/components/workspace/WorkspaceTabBar";
import { WorkspaceSheet } from "@/components/workspace/WorkspaceSheet";
import { SourceViewer } from "@/components/workspace/SourceViewer";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { ModeSwitcher } from "@/components/workspace/ModeSwitcher";
import { useMediaQuery } from "@/hooks/useMediaQuery";

export function WorkspaceLayout({ artifactId }: { artifactId?: string }) {
  const [mode, setMode] = useState<"learn" | "test">("learn");
  const [pane, setPane] = useState<WorkspacePane>("mcq");
  const isLg = useMediaQuery("(min-width: 1024px)");

  if (mode === "test") {
    return (
      <div className="workspace workspace--test">
        <header className="workspace__header">
          <ModeSwitcher mode={mode} onChange={setMode} />
        </header>
        <McqPanel artifactId={artifactId} mode="test" />
      </div>
    );
  }

  return (
    <div className="workspace workspace--learn">
      <header className="workspace__header">
        <ModeSwitcher mode={mode} onChange={setMode} />
        <span className="workspace__title">Question Better.</span>
      </header>
      <div className="workspace__body">
        {isLg ? (
          <div className="workspace__desktop-split">
            <McqPanel artifactId={artifactId} mode="learn" />
            <div className="workspace__right">
              <SourceViewer artifactId={artifactId} />
              <ChatPanel artifactId={artifactId} />
            </div>
          </div>
        ) : (
          <>
            {pane === "mcq" && <McqPanel artifactId={artifactId} mode="learn" />}
            {pane !== "mcq" && (
              <WorkspaceSheet onClose={() => setPane("mcq")}>
                {pane === "source" ? (
                  <SourceViewer artifactId={artifactId} />
                ) : (
                  <ChatPanel artifactId={artifactId} />
                )}
              </WorkspaceSheet>
            )}
          </>
        )}
      </div>
      {!isLg && <WorkspaceTabBar active={pane} onChange={setPane} />}
    </div>
  );
}
