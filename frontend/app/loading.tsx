import { Box, Center, Loader, Stack, Text } from "@mantine/core";
import { BrandMark } from "@/app/_components/BrandMark";

/**
 * Route-level loading state. A calm paper skeleton — used by Next.js while a
 * route segment is fetching.
 */
export default function Loading() {
  return (
    <Box bg="var(--mantine-color-body)" style={{ minHeight: "100dvh" }}>
      <Center style={{ minHeight: "100dvh" }} p="md">
        <Stack gap="lg" align="center" ta="center">
          <BrandMark height={36} />
          <Loader size="sm" color="lavender" type="dots" />
          <Text
            size="sm"
            c="gray.5"
            fs="italic"
            style={{ fontFamily: "var(--font-serif)", letterSpacing: "0.02em" }}
          >
            Turning ink into understanding…
          </Text>
        </Stack>
      </Center>
    </Box>
  );
}
