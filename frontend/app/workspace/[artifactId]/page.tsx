import { WorkspaceLayout } from "@/components/workspace/WorkspaceLayout";

export default async function WorkspaceArtifactPage({
  params,
}: {
  params: Promise<{ artifactId: string }>;
}) {
  const { artifactId } = await params;
  return <WorkspaceLayout artifactId={artifactId} />;
}
