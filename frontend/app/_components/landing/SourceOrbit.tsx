"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Box, Button, Container, Group, Stack, Text } from "@mantine/core";
import {
  IconArrowRight,
  IconFileTypePdf,
  IconBrandYoutube,
  IconBrandGithub,
  IconPresentation,
  IconFileText,
  IconWorld,
  IconMicrophone,
  IconPhoto,
  IconNotebook,
  type Icon,
} from "@tabler/icons-react";
import { useReducedMotion } from "framer-motion";
import { Reveal } from "./motion";

/**
 * Wispr-Flow's "Write faster in all your apps, on any device" section, adapted
 * for Zivo: a dark showcase panel with a device mock running the study UI,
 * orbited by an arc of source-type icons (PDF, YouTube, slides, repos, notes…) -
 * "drop in any source, get questions." The panel is intentionally dark in BOTH
 * colour schemes (echoing Wispr's dark band) so colours are pinned literally.
 */

type Source = { icon: Icon; color: string; label: string };

const SOURCES: Source[] = [
  { icon: IconFileTypePdf, color: "#E5484D", label: "PDF" },
  { icon: IconBrandYoutube, color: "#FF3B30", label: "YouTube" },
  { icon: IconPresentation, color: "#F2A900", label: "Slides" },
  { icon: IconFileText, color: "#2B7FFF", label: "Docs" },
  { icon: IconBrandGithub, color: "#1A1917", label: "Repos" },
  { icon: IconWorld, color: "#0E9F9F", label: "Web" },
  { icon: IconMicrophone, color: "#7B5DA6", label: "Audio" },
  { icon: IconPhoto, color: "#3FA34D", label: "Images" },
  { icon: IconNotebook, color: "#46433D", label: "Notes" },
];

const RADIUS = 200;
const VISUAL = 480;

export function SourceOrbit() {
  const reduce = useReducedMotion();
  // The orbit visual is laid out at a fixed 480px (icons sit at a fixed radius).
  // On narrow screens that overflowed and got clipped - so scale the whole visual
  // down proportionally to the space available, keeping every icon on-screen.
  const orbitWrapRef = useRef<HTMLDivElement>(null);
  const [orbitScale, setOrbitScale] = useState(1);
  useEffect(() => {
    const el = orbitWrapRef.current;
    if (!el) return;
    const measure = () => setOrbitScale(Math.min(1, el.clientWidth / VISUAL));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: 24, md: 48 }}>
      <Box
        style={{
          background: "#161512",
          borderRadius: 28,
          border: "1px solid #2A2824",
          overflow: "hidden",
          boxShadow: "var(--mantine-shadow-paper-lg)",
        }}
      >
        <Group
          align="center"
          justify="space-between"
          wrap="wrap"
          gap={32}
          p={{ base: 24, md: 56 }}
        >
          {/* Left - copy */}
          <Stack gap={22} maw={440} miw={0} style={{ flex: "1 1 280px" }}>
            <Reveal>
              <Group gap={8} wrap="wrap">
                {["Mac", "Windows", "iPhone", "Android"].map((p) => (
                  <Box
                    key={p}
                    style={{
                      padding: "5px 12px",
                      borderRadius: 999,
                      border: "1px solid #3A382F",
                      color: "#D9D3C4",
                      fontFamily: "var(--font-sans), sans-serif",
                      fontSize: 12,
                      fontWeight: 600,
                    }}
                  >
                    {p}
                  </Box>
                ))}
              </Group>
            </Reveal>
            <Reveal>
              <Box
                component="h2"
                style={{
                  margin: 0,
                  color: "#FAF9F6",
                  fontWeight: 500,
                  fontSize: "clamp(1.9rem, 4vw, 3rem)",
                  lineHeight: 1.1,
                  letterSpacing: "-0.015em",
                }}
              >
                Study anything,
                <br />
                on any device.
              </Box>
            </Reveal>
            <Reveal>
              <Text size="lg" lh={1.6} style={{ color: "#A8A296" }}>
                Drop in a PDF, a lecture recording, a YouTube link, a repo, or your
                own notes. Zivo reads it and turns it into exam-ready questions -
                everywhere you study.
              </Text>
            </Reveal>
            <Reveal>
              <Button
                component={Link}
                href="/workspace"
                size="md"
                rightSection={<IconArrowRight size={18} stroke={2} />}
                className="orbit-cta"
              >
                Start studying
              </Button>
            </Reveal>
          </Stack>

          {/* Right - device + orbiting source icons, scaled to fit any width */}
          <Box
            ref={orbitWrapRef}
            style={{
              position: "relative",
              width: "100%",
              maxWidth: VISUAL,
              height: VISUAL * orbitScale,
              flex: "1 1 260px",
              minWidth: 0,
              margin: "0 auto",
            }}
          >
          <Box
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: VISUAL,
              height: VISUAL,
              transformOrigin: "top left",
              transform: `scale(${orbitScale})`,
            }}
          >
            {/* faint orbit guide rings */}
            <Box
              aria-hidden
              style={{
                position: "absolute",
                inset: `calc(50% - ${RADIUS}px)`,
                borderRadius: "50%",
                border: "1px dashed #322F29",
              }}
            />

            {/* orbiting icons */}
            <Box
              className={reduce ? undefined : "orbit-ring"}
              style={{ position: "absolute", inset: 0 }}
            >
              {SOURCES.map((s, i) => {
                const angle = (360 / SOURCES.length) * i;
                return (
                  <Box
                    key={s.label}
                    style={{
                      position: "absolute",
                      top: "50%",
                      left: "50%",
                      transform: `rotate(${angle}deg) translateX(${RADIUS}px) rotate(-${angle}deg)`,
                    }}
                  >
                    <Box
                      className={reduce ? undefined : "orbit-spin-rev"}
                      style={{
                        width: 52,
                        height: 52,
                        marginLeft: -26,
                        marginTop: -26,
                        borderRadius: 14,
                        background: "#FFFFFF",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        boxShadow: "0 6px 18px rgba(0,0,0,0.35)",
                      }}
                    >
                      <s.icon size={26} stroke={1.7} color={s.color} />
                    </Box>
                  </Box>
                );
              })}
            </Box>

            {/* device mock */}
            <Box
              style={{
                position: "absolute",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                width: 188,
                height: 384,
                borderRadius: 34,
                background: "#0C0B09",
                border: "6px solid #26241F",
                boxShadow: "0 24px 60px rgba(0,0,0,0.5)",
                padding: 12,
                display: "flex",
              }}
            >
              <Box
                style={{
                  flex: 1,
                  borderRadius: 22,
                  background: "var(--mantine-color-body)",
                  padding: 14,
                  display: "flex",
                  flexDirection: "column",
                  gap: 10,
                  overflow: "hidden",
                }}
              >
                <Text
                  size="9px"
                  fw={700}
                  tt="uppercase"
                  lts={1}
                  c="lavender.7"
                  style={{ fontFamily: "var(--font-sans)" }}
                >
                  Question 3 of 8
                </Text>
                <Text
                  fw={600}
                  c="gray.8"
                  style={{ fontFamily: "var(--font-sans)", fontSize: 13, lineHeight: 1.3 }}
                >
                  Where does the citric acid cycle take place?
                </Text>
                <Stack gap={7} mt={2}>
                  {[
                    { t: "Cytoplasm", ok: false },
                    { t: "Mitochondrial matrix", ok: true },
                    { t: "Cell membrane", ok: false },
                  ].map((o) => (
                    <Box
                      key={o.t}
                      style={{
                        padding: "8px 10px",
                        borderRadius: 9,
                        fontFamily: "var(--font-sans)",
                        fontSize: 11,
                        fontWeight: o.ok ? 600 : 500,
                        color: o.ok
                          ? "var(--mantine-color-lavender-7)"
                          : "var(--mantine-color-gray-7)",
                        background: o.ok
                          ? "var(--mantine-color-lavender-0)"
                          : "var(--mantine-color-gray-1)",
                        border: o.ok
                          ? "1.5px solid var(--mantine-color-lavender-5)"
                          : "1px solid var(--mantine-color-default-border)",
                      }}
                    >
                      {o.t}
                    </Box>
                  ))}
                </Stack>
              </Box>
            </Box>
          </Box>
          </Box>
        </Group>
      </Box>

      <style>{`
        .orbit-cta {
          background-color: #E9DDF5 !important;
          color: #232220 !important;
          border-radius: 999px !important;
          font-family: var(--font-sans), sans-serif !important;
          font-weight: 600 !important;
          border: 1px solid #D6C2EC !important;
        }
        .orbit-cta:hover { background-color: #DFCDED !important; }
        .orbit-ring {
          animation: orbit-spin 60s linear infinite;
          transform-origin: 50% 50%;
        }
        .orbit-spin-rev {
          animation: orbit-spin-rev 60s linear infinite;
          transform-origin: 50% 50%;
        }
        @keyframes orbit-spin { to { transform: rotate(360deg); } }
        @keyframes orbit-spin-rev { to { transform: rotate(-360deg); } }
        @media (prefers-reduced-motion: reduce) {
          .orbit-ring, .orbit-spin-rev { animation: none !important; }
        }
      `}</style>
    </Container>
  );
}
