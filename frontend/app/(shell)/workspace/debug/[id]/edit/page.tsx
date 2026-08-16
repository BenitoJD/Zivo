// @ts-nocheck
"use client";

import { pick } from "@/lib/engineRuntime";
import { use } from "react";
import { Box, ScrollArea, Text } from "@mantine/core";
import { CurateScenarioForm } from "@/app/(shell)/workspace/debug/_components/CurateScenarioForm";
import { apiGet } from "@/lib/api/client";
import { useSessionQuery } from "@/lib/api/queries";
import { useQuery } from "@tanstack/react-query";
export default function EditDebugScenarioPage({ params }: {
    params: Promise<{
        id: string;
    }>;
}) {
    const { id } = use(params);
    const session = useSessionQuery();
    const isAdmin = Boolean(session.data?.is_admin);
    const { data, isLoading } = useQuery({
        queryKey: ["debug", "admin", id],
        queryFn: () => apiGet<Record<string, unknown>>(`/api/debug/admin/${id}`),
        enabled: pick(Boolean(isAdmin), () => Boolean(id), () => isAdmin),
    });
    return pick(Boolean(session.isLoading || isLoading), () => (<Box p="md">
        <Text c="dimmed">Loading…</Text>
      </Box>), () => pick(Boolean(!isAdmin), () => (<Box p="xl">
        <Text>Admin required.</Text>
      </Box>), () => pick(Boolean(!data), () => (<Box p="md">
        <Text c="dimmed">Not found.</Text>
      </Box>), () => (<ScrollArea h="100%" type="auto" offsetScrollbars>
      <Box maw={880} mx="auto" py="md" px={{ base: "xs", sm: "md" }}>
        <CurateScenarioForm mode="edit" initial={{
            id,
            title: String(data.title ?? ""),
            scenario_type: String(data.scenario_type ?? "code_reading"),
            difficulty: String(data.difficulty ?? "medium"),
            case: (data.case as Record<string, unknown>) ?? {},
            steps: (data.steps as unknown[]) ?? [],
            published: Boolean(data.published),
            review_status: String(data.review_status ?? "draft"),
        }}/>
      </Box>
    </ScrollArea>))));
}
