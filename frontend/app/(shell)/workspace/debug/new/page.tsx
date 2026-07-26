"use client";

import { Box, ScrollArea, Text } from "@mantine/core";
import { CurateScenarioForm } from "@/app/(shell)/workspace/debug/_components/CurateScenarioForm";
import { useSessionQuery } from "@/lib/api/queries";

export default function NewDebugScenarioPage() {
  const session = useSessionQuery();
  const isAdmin = Boolean(session.data?.is_admin);

  if (session.isLoading) {
    return (
      <Box p="md">
        <Text c="dimmed">Loading…</Text>
      </Box>
    );
  }
  if (!isAdmin) {
    return (
      <Box p="xl">
        <Text>Admin required to curate debug scenarios.</Text>
      </Box>
    );
  }

  return (
    <ScrollArea h="100%" type="auto" offsetScrollbars>
      <Box maw={880} mx="auto" py="md" px={{ base: "xs", sm: "md" }}>
        <CurateScenarioForm mode="create" />
      </Box>
    </ScrollArea>
  );
}
