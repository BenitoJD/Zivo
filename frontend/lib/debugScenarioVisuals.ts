import type { TablerIcon } from "@tabler/icons-react";
import {
  IconApi,
  IconArrowsShuffle,
  IconBug,
  IconCode,
  IconFileText,
  IconSettings,
  IconStethoscope,
  IconTestPipe,
} from "@tabler/icons-react";

export type DebugScenarioVisual = {
  icon: TablerIcon;
  color: string;
  label: string;
};

const BY_TYPE: Record<string, DebugScenarioVisual> = {
  code_reading: { icon: IconCode, color: "lavender", label: "Code" },
  stack_trace: { icon: IconBug, color: "terracotta", label: "Stack trace" },
  log_analysis: { icon: IconFileText, color: "forest", label: "Logs" },
  test_failure: { icon: IconTestPipe, color: "terracotta", label: "Tests" },
  config_error: { icon: IconSettings, color: "gray", label: "Config" },
  concurrency: { icon: IconArrowsShuffle, color: "lavender", label: "Concurrency" },
  api_contract: { icon: IconApi, color: "forest", label: "API" },
  debug_process: { icon: IconStethoscope, color: "sage", label: "Process" },
};

const DEFAULT: DebugScenarioVisual = {
  icon: IconBug,
  color: "lavender",
  label: "Debug",
};

export function debugScenarioVisual(scenarioType: string): DebugScenarioVisual {
  return BY_TYPE[scenarioType] ?? DEFAULT;
}
