"use client";

import { Box, Container, Stack, Text, Title, Accordion } from "@mantine/core";
import { Reveal } from "./motion";

const FAQS: { q: string; a: string }[] = [
  {
    q: "What can I upload?",
    a: "Anything you study from - PDFs (textbooks, papers, lecture notes), slide decks, articles, your own notes, and even GitHub repositories. Zivo reads the material and gets it ready for questions.",
  },
  {
    q: "How accurate are the questions and explanations?",
    a: "Questions are scoped exactly to the pages you choose, and the tutor answers only from your uploaded source - so it won't invent facts from the open internet. As with any AI tool, treat answers as a study aid and verify against your material when it matters.",
  },
  {
    q: "Who is Zivo for?",
    a: "Students preparing for exams, researchers working through dense papers, and anyone trying to genuinely understand a topic rather than just re-read it. If you learn from documents, Zivo is built for you.",
  },
  {
    q: "Is it free to start?",
    a: "Yes. You can upload a source and start studying in under a minute - no credit card required to begin.",
  },
  {
    q: "Does my uploaded material stay private?",
    a: "Your sources are stored in your own workspace and used only to generate your study material. We don't sell your data.",
  },
];

/**
 * Honest FAQ using Mantine's Accordion. Keeps the calm paper treatment and a
 * single open item at a time.
 */
export function Faq() {
  return (
    <Container size="md" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 80 }}>
      <Stack gap={36}>
        <Reveal>
          <Title
            order={2}
            ta="center"
            style={{
              fontFamily: "var(--font-sans), sans-serif",
              fontWeight: 500,
              fontSize: "clamp(1.6rem, 3.2vw, 2.3rem)",
              letterSpacing: "-0.015em",
            }}
          >
            Questions, answered.
          </Title>
        </Reveal>

        <Reveal>
          <Box
            style={{
              background: "var(--mantine-color-gray-0)",
              borderRadius: "var(--mantine-radius-xl)",
              border: "1px solid var(--mantine-color-default-border)",
              overflow: "hidden",
            }}
          >
            <Accordion chevronPosition="right" variant="separated" radius="md">
              {FAQS.map((item) => (
                <Accordion.Item key={item.q} value={item.q} bg="transparent">
                  <Accordion.Control
                    style={{
                      fontFamily: "var(--font-sans)",
                      fontWeight: 600,
                      fontSize: "1rem",
                    }}
                  >
                    {item.q}
                  </Accordion.Control>
                  <Accordion.Panel>
                    <Text size="sm" c="gray.6" lh={1.65}>
                      {item.a}
                    </Text>
                  </Accordion.Panel>
                </Accordion.Item>
              ))}
            </Accordion>
          </Box>
        </Reveal>
      </Stack>
    </Container>
  );
}
