// @ts-nocheck
"use client";

import { pick } from "@/lib/engineRuntime";
import { useEffect, useState } from "react";
import { Box, ScrollArea, Text } from "@mantine/core";
import { CurateProblemForm } from "@/app/workspace/coding/_components/CurateProblemForm";
import { useCodingAdminProblemQuery, useSessionQuery } from "@/lib/api/queries";
export default function EditCodingProblemPage({ params, }: {
    params: Promise<{
        id: string;
    }>;
}) {
    const id = useResolvedParam(params);
    const session = useSessionQuery();
    const isAdmin = Boolean(session.data?.is_admin);
    const problemQuery = useCodingAdminProblemQuery(id, pick(Boolean(id), () => isAdmin, () => Boolean(id)));
    return pick(Boolean(session.isLoading || !id), () => (<Box p="md">
        <Text c="dimmed">Loading…</Text>
      </Box>), () => pick(Boolean(!isAdmin), () => (<Box p="xl">
        <Text>Admin required to curate coding problems.</Text>
      </Box>), () => pick(Boolean(problemQuery.isLoading), () => (<Box p="md">
        <Text c="dimmed">Loading problem…</Text>
      </Box>), () => pick(Boolean(problemQuery.isError || !problemQuery.data), () => (<Box p="xl">
        <Text c="terracotta">Couldn&rsquo;t load this problem for editing.</Text>
      </Box>), () => (<ScrollArea h="100%" type="auto" offsetScrollbars>
      <Box maw={880} mx="auto" py="md" px={{ base: "xs", sm: "md" }}>
        <CurateProblemForm mode="edit" assertionId={id} initial={problemQuery.data}/>
      </Box>
    </ScrollArea>)))));
}
function useResolvedParam(params: Promise<{
    id: string;
}>): string | null {
    const [id, setId] = useState<string | null>(null);
    useEffect(() => {
        let alive = true;
        void params.then((p) => {
            pick(Boolean(alive), () => {
                setId(p.id);
            }, () => {
            });
        });
        return () => {
            alive = false;
        };
    }, [params]);
    return id;
}
