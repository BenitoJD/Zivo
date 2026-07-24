"use client";

import { useRouter } from "next/navigation";
import { Alert, Button, Center, Stack } from "@mantine/core";

export default function ArtifactError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const router = useRouter();

  return (
    <Center mih="50vh" p="md">
      <Stack gap="md" maw={420} w="100%">
        <Alert color="red" title="Could not load workspace" variant="light">
          {error.message || "Something went wrong while loading this source."}
        </Alert>
        <Stack gap="xs">
          <Button onClick={reset}>Try again</Button>
          <Button variant="default" onClick={() => router.push("/workspace")}>
            Back to library
          </Button>
        </Stack>
      </Stack>
    </Center>
  );
}
