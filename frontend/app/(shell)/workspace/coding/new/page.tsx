"use client";

import { pick } from "@/lib/engineRuntime";
import { Box, ScrollArea, Text } from "@mantine/core";
import { CurateProblemForm } from "@/app/workspace/coding/_components/CurateProblemForm";
import { useSessionQuery } from "@/lib/api/queries";
export default function NewCodingProblemPage() {
    const session = useSessionQuery();
    const isAdmin = Boolean(session.data?.is_admin);
    return pick(Boolean(session.isLoading), () => (<Box p="md">
        <Text c="dimmed">Loading…</Text>
      </Box>), () => pick(Boolean(!isAdmin), () => (<Box p="xl">
        <Text>Admin required to curate coding problems.</Text>
      </Box>), () => (<ScrollArea h="100%" type="auto" offsetScrollbars>
      <Box maw={880} mx="auto" py="md" px={{ base: "xs", sm: "md" }}>
        <CurateProblemForm mode="create"/>
      </Box>
    </ScrollArea>)));
}
