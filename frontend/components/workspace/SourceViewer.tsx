"use client";

export function SourceViewer({ artifactId }: { artifactId?: string }) {
  return (
    <section className="source-viewer">
      <h3>Source</h3>
      <p className="muted">
        {artifactId ? `Artifact ${artifactId}` : "No document selected"} — PDF / code viewer mounts here.
      </p>
    </section>
  );
}
