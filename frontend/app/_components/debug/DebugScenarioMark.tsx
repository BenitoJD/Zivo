import { Paper, ThemeIcon } from "@mantine/core";
import { debugScenarioVisual } from "@/lib/debugScenarioVisuals";

type Props = {
  scenarioType: string;
  size?: number;
};

/** Scenario-type tile for debug practice cards. */
export function DebugScenarioMark({ scenarioType, size = 52 }: Props) {
  const visual = debugScenarioVisual(scenarioType);
  const Icon = visual.icon;
  const iconSize = Math.round(size * 0.46);

  return (
    <Paper
      radius="lg"
      withBorder
      bg="gray.0"
      shadow="paper"
      w={size}
      h={size}
      style={{
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <ThemeIcon size={iconSize + 12} radius="md" variant="light" color={visual.color}>
        <Icon size={iconSize} stroke={1.6} />
      </ThemeIcon>
    </Paper>
  );
}
