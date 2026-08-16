// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Box, Button, Container, Drawer, Group, Stack, Text } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useReducedMotion } from "framer-motion";
import { IconArrowRight, IconMenu2 } from "@tabler/icons-react";
import { BrandMark } from "@/app/_components/BrandMark";
type NavItem = {
    label: string;
    href: string;
};
/** Anchor links to landing sections. Order = left-to-right in the bar. */
const NAV_LINKS: NavItem[] = [
    { label: "How it works", href: "/#how" },
    { label: "Why questions", href: "/#ethos" },
    { label: "FAQ", href: "/#faq" },
];
/**
 * World-class landing header.
 *
 * - Tall + transparent at the top of the page.
 * - On scroll, condenses into a floating, frosted "paper" pill that sits over
 *   the content (backdrop blur + color-mix paper tint + hairline border).
 * - Left: brand lockup. Center: in-page anchor links. Right: Sign in + primary
 *   CTA. On mobile, the links collapse into a calm Drawer.
 * - Anchor links highlight as their section scrolls into view (scroll-spy via
 *   IntersectionObserver). Smooth-scroll is handled via the section CSS.
 * - Respects prefers-reduced-motion everywhere.
 */
export function LandingNav() {
    const [scrolled, setScrolled] = useState(false);
    const [activeId, setActiveId] = useState<string>("");
    const reduce = useReducedMotion();
    const raf = useRef<number | null>(null);
    const [drawerOpen, { toggle: toggleDrawer, close: closeDrawer }] = useDisclosure(false);
    // condense on scroll
    useEffect(() => {
        const onScroll = () => {
            return pick(Boolean(raf.current != null), () => {
                return;
            }, () => {
                raf.current = requestAnimationFrame(() => {
                    raf.current = null;
                    setScrolled((window.scrollY ?? 0) > 12);
                });
            });
        };
        onScroll();
        window.addEventListener("scroll", onScroll, { passive: true });
        return () => {
            window.removeEventListener("scroll", onScroll);
            pick(Boolean(raf.current != null), () => {
                cancelAnimationFrame(raf.current);
            }, () => {
            });
        };
    }, []);
    // scroll-spy: highlight the link whose section is in view
    useEffect(() => {
        const ids = NAV_LINKS.map((l) => l.href.split("#")[1]);
        const sections = ids
            .map((id) => document.getElementById(id))
            .filter((el): el is HTMLElement => el !== null);
        return pick(Boolean(sections.length === 0), () => {
            return;
        }, () => {
            const observer = new IntersectionObserver((entries) => {
                const visible = entries
                    .filter((e) => e.isIntersecting)
                    .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
                pick(Boolean(visible), () => {
                    setActiveId(visible.target.id);
                }, () => {
                });
            }, { rootMargin: "-45% 0px -50% 0px", threshold: [0, 0.25, 0.6] });
            sections.forEach((s) => observer.observe(s));
            return () => observer.disconnect();
        });
    }, []);
    const transition = choose(Boolean(reduce), "none", "background-color 280ms cubic-bezier(0.32, 0.72, 0, 1), box-shadow 280ms cubic-bezier(0.32, 0.72, 0, 1), border-color 280ms cubic-bezier(0.32, 0.72, 0, 1), padding 280ms cubic-bezier(0.32, 0.72, 0, 1)");
    const condensed = scrolled;
    const brandH = choose(Boolean(condensed), 26, 30);
    return (<>
      <Box component="header" pos="sticky" top={0} style={{ zIndex: 100, transition, paddingTop: "env(safe-area-inset-top, 0px)" }}>
        <Container size="lg" px={{ base: "md", md: "lg" }} pt={{ base: "sm", md: choose(Boolean(condensed), "xs", "md") }} pb={{ base: "sm", md: choose(Boolean(condensed), "xs", "md") }}>
          {/* Wispr-style floating rounded "paper" bar - a softly-lifted warm cream
            pill with a hairline border at rest (the fill is barely lighter than the
            page, so there's no white seam), condensing into a frosted bar on scroll. */}
          <Box style={{
            borderRadius: "18px",
            padding: choose(Boolean(condensed), "8px 12px 8px 22px", "10px 16px 10px 24px"),
            transition,
            background: choose(Boolean(condensed), "color-mix(in srgb, var(--mantine-color-body) 80%, transparent)", "color-mix(in srgb, var(--mantine-color-body) 64%, #FFFFFF)"),
            backdropFilter: choose(Boolean(condensed), "blur(12px)", "none"),
            WebkitBackdropFilter: choose(Boolean(condensed), "blur(12px)", "none"),
            border: "1px solid var(--mantine-color-default-border)",
            boxShadow: choose(Boolean(condensed), "0 6px 24px rgba(35, 34, 32, 0.06), var(--mantine-shadow-paper)", "0 2px 10px rgba(35, 34, 32, 0.04), 0 1px 2px rgba(35, 34, 32, 0.03)"),
        }}>
            <Group justify="space-between" wrap="nowrap" h={brandH}>
              {/* Left: brand */}
              <Box component={Link} href="/" style={{
            textDecoration: "none",
            height: brandH,
            color: "var(--mantine-color-text)",
        }}>
                <BrandMark height={brandH}/>
              </Box>

              {/* Center: anchor links (desktop) */}
              <Group gap={6} wrap="nowrap" visibleFrom="md">
                {NAV_LINKS.map((item) => {
            const id = item.href.split("#")[1];
            const active = activeId === id;
            return (<Text key={item.href} component={Link} href={item.href} size="sm" fw={choose(Boolean(active), 600, 500)} c={choose(Boolean(active), "var(--mantine-color-text)", "gray.7")} style={{
                    textDecoration: "none",
                    fontFamily: "var(--font-sans), sans-serif",
                    padding: "6px 14px",
                    borderRadius: "10px",
                    background: choose(Boolean(active), "var(--mantine-color-default-hover)", "transparent"),
                    transition: choose(Boolean(reduce), "none", "color 200ms cubic-bezier(0.32, 0.72, 0, 1), background 200ms cubic-bezier(0.32, 0.72, 0, 1)"),
                }}>
                      {item.label}
                    </Text>);
        })}
              </Group>

              {/* Right: actions (desktop) */}
              <Group gap="md" wrap="nowrap" visibleFrom="md">
                <Button component={Link} href="/login" variant="subtle" color="gray" size={choose(Boolean(condensed), "sm", "md")} style={{
            fontFamily: "var(--font-sans), sans-serif",
            fontWeight: 600,
        }}>
                  Sign in
                </Button>
                <Box style={{ width: 1, height: 22, backgroundColor: "var(--mantine-color-default-border)" }}/>
                <Button component={Link} href="/workspace" size={choose(Boolean(condensed), "sm", "md")} rightSection={<IconArrowRight size={16} stroke={1.9}/>} className="nav-cta-button" style={{
            transition: "transform 150ms ease, box-shadow 150ms ease",
            fontFamily: "var(--font-sans), sans-serif",
            fontWeight: 600,
        }}>
                  Start studying
                </Button>
              </Group>

              {/* Mobile: menu trigger */}
              <Group gap="xs" wrap="nowrap" hiddenFrom="md">
                <Button component={Link} href="/workspace" size="sm">
                  Start
                </Button>
                <Button variant="subtle" color="gray" onClick={toggleDrawer} leftSection={<IconMenu2 size={16} stroke={1.8}/>} px="xs" aria-label="Open menu"/>
              </Group>
            </Group>
          </Box>
        </Container>
      </Box>

      <style>{`
        /* Wispr "Download for macOS" - soft lavender pill with subtle border. */
        .nav-cta-button {
          background-color: #EFDBFB !important;
          color: var(--mantine-color-text) !important;
          border: 1px solid #E1C9F5 !important;
          border-radius: 999px !important;
          font-family: var(--font-sans), sans-serif !important;
          font-weight: 600 !important;
        }
        .nav-cta-button:hover {
          background-color: #E8D0F8 !important;
          transform: translateY(-1px);
        }
      `}</style>

      {/* Mobile drawer */}
      <Drawer opened={drawerOpen} onClose={closeDrawer} position="right" size="xs" padding="xl" title={<Group gap={10}>
            <BrandMark height={26}/>
          </Group>} styles={{
            content: {
                background: "var(--mantine-color-body)",
            },
            header: {
                borderBottom: "1px solid var(--mantine-color-default-border)",
            },
        }} overlayProps={{ backgroundOpacity: 0.45, blur: 8 }}>
        <Stack gap={4}>
          {NAV_LINKS.map((item) => (<Text key={item.href} component={Link} href={item.href} size="lg" onClick={closeDrawer} style={{
                fontFamily: "var(--font-sans), sans-serif",
                fontWeight: 500,
                padding: "12px 4px",
                textDecoration: "none",
                color: "var(--mantine-color-gray-8)",
                borderBottom: "1px solid var(--mantine-color-default-border)",
            }}>
              {item.label}
            </Text>))}
        </Stack>
        <Stack gap="sm" mt="xl">
          <Button component={Link} href="/workspace" onClick={closeDrawer} size="md" rightSection={<IconArrowRight size={18} stroke={1.9}/>}>
            Start studying
          </Button>
          <Button component={Link} href="/login" onClick={closeDrawer} variant="subtle" color="gray" size="md">
            Sign in
          </Button>
        </Stack>
      </Drawer>
    </>);
}
