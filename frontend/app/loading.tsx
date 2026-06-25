import { Box, Center, Skeleton, Stack } from "@mantine/core";

/**
 * Route-level loading state. A calm paper skeleton — used by Next.js while a
 * route segment is fetching.
 */
export default function Loading() {
  return (
    <Box bg="var(--mantine-color-body)" style={{ minHeight: "100dvh" }}>
      <Center style={{ minHeight: "100dvh" }} p="md">
        <Stack gap="md" w="100%" maw={560}>
          <Skeleton height={28} width="40%" radius="md" />
          <Skeleton height={64} radius="md" />
          <Skeleton height={64} radius="md" />
          <Skeleton height={64} radius="md" />
        </Stack>
      </Center>
    </Box>
  );
}
