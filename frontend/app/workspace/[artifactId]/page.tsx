import { ArtifactSetup } from "@/components/workspace/ArtifactSetup";
import { WorkspaceLayout } from "@/components/workspace/WorkspaceLayout";

export default async function WorkspaceArtifactPage({
  params,
}: {
  params: Promise<{ artifactId: string }>;
}) {
  const { artifactId } = await params;
  return (
    <ArtifactSetup artifactId={artifactId}>
      <WorkspaceLayout artifactId={artifactId} />
    </ArtifactSetup>
  );
}
