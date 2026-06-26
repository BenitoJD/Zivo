"use client";

import { useState } from "react";
import {
  Box,
  Button,
  Group,
  Modal,
  Progress,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import {
  IconBookUpload,
  IconBulb,
  IconCheck,
  IconChevronLeft,
  IconChevronRight,
  IconMessageCircle,
  IconSparkles,
} from "@tabler/icons-react";

type OnboardingGuideProps = {
  opened: boolean;
  onClose: () => void;
};

const STEPS = [
  {
    title: "Welcome to Zivo",
    body: " Zivo turns your textbooks, PDFs, and notes into high-quality practice questions to help you measure and improve your understanding.",
    icon: IconSparkles,
    color: "lavender",
  },
  {
    title: "1. Upload Your Material",
    body: "Click 'Add Source' to upload textbooks, articles, lecture slides, or paste notes directly. Zivo indexes your source securely.",
    icon: IconBookUpload,
    color: "lavender",
  },
  {
    title: "2. Generate Concept Questions",
    body: "Select specific pages to target your studying. Zivo generates exam-style multiple choice questions grounded in that exact text.",
    icon: IconBulb,
    color: "sage",
  },
  {
    title: "3. Chat with Your Source Tutor",
    body: "Got something wrong? Ask follow-up questions in plain language. The tutor answers solely using your uploaded material—no guessing.",
    icon: IconMessageCircle,
    color: "lavender",
  },
];

export function OnboardingGuide({ opened, onClose }: OnboardingGuideProps) {
  const [activeStep, setActiveStep] = useState(0);

  // Mark onboarding done on ANY close (finish, escape, or overlay click) so it
  // only ever appears once for a new user.
  const dismiss = () => {
    try {
      localStorage.setItem("zivo-onboarding-completed", "true");
    } catch {
      /* ignore */
    }
    onClose();
  };

  const handleNext = () => {
    if (activeStep < STEPS.length - 1) {
      setActiveStep((prev) => prev + 1);
    } else {
      dismiss();
    }
  };

  const handlePrev = () => {
    if (activeStep > 0) {
      setActiveStep((prev) => prev - 1);
    }
  };

  const current = STEPS[activeStep];
  const StepIcon = current.icon;
  const pct = ((activeStep + 1) / STEPS.length) * 100;

  return (
    <Modal
      opened={opened}
      onClose={dismiss}
      withCloseButton={false}
      centered
      size="md"
      overlayProps={{
        backgroundOpacity: 0.45,
        blur: 8,
      }}
      styles={{
        content: {
          borderRadius: "var(--mantine-radius-xl)",
          background: "var(--mantine-color-body)",
          border: "1px solid var(--mantine-color-default-border)",
          overflow: "hidden",
        },
      }}
    >
      <Box style={{ position: "relative", padding: "var(--mantine-spacing-lg) 0" }}>
        {/* Step indicator bar */}
        <Progress value={pct} size="xs" color="lavender" mb="xl" style={{ borderRadius: 2 }} />

        <style>{`
          @keyframes zivo-ob-step { from { opacity: 0; transform: translateX(16px); } to { opacity: 1; transform: none; } }
          .zivo-ob-step { animation: zivo-ob-step 320ms cubic-bezier(0.32,0.72,0,1) both; }
          @media (prefers-reduced-motion: reduce) { .zivo-ob-step { animation: none; } }
        `}</style>
        <div key={activeStep} className="zivo-ob-step">
            <Stack align="center" gap="lg" px="sm" ta="center">
              <ThemeIcon
                size={64}
                radius="xl"
                variant="light"
                color={current.color}
                style={{
                  background: `var(--mantine-color-${current.color}-0)`,
                  border: `1.5px dashed var(--mantine-color-${current.color}-3)`,
                }}
              >
                <StepIcon size={32} stroke={1.5} style={{ color: `var(--mantine-color-${current.color}-7)` }} />
              </ThemeIcon>

              <Stack gap="xs">
                <Title
                  order={3}
                  style={{
                    fontFamily: "var(--font-serif), Georgia, serif",
                    fontWeight: 500,
                    letterSpacing: "-0.01em",
                    fontSize: "1.45rem",
                  }}
                >
                  {current.title}
                </Title>
                <Text size="sm" c="gray.6" lh={1.6} maw={380}>
                  {current.body}
                </Text>
              </Stack>
            </Stack>
        </div>

        <Group justify="space-between" mt={48} px="sm">
          <Button
            variant="subtle"
            color="gray"
            disabled={activeStep === 0}
            onClick={handlePrev}
            leftSection={<IconChevronLeft size={16} />}
          >
            Back
          </Button>

          <Button
            onClick={handleNext}
            color="lavender"
            rightSection={activeStep === STEPS.length - 1 ? <IconCheck size={16} /> : <IconChevronRight size={16} />}
          >
            {activeStep === STEPS.length - 1 ? "Finish guide" : "Next step"}
          </Button>
        </Group>
      </Box>
    </Modal>
  );
}
