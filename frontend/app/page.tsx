"use client";

import { Box } from "@mantine/core";
import { ScrollViewport } from "@/app/_components/ScrollViewport";
import { LandingNav } from "@/app/_components/landing/LandingNav";
import { Hero } from "@/app/_components/landing/Hero";
import { SourceOrbit } from "@/app/_components/landing/SourceOrbit";
import { LogoStrip } from "@/app/_components/landing/LogoStrip";
import { SpeedStats } from "@/app/_components/landing/SpeedStats";
import { Personas } from "@/app/_components/landing/Personas";
import { Marquee } from "@/app/_components/landing/Marquee";
import { Capabilities } from "@/app/_components/landing/Capabilities";
import { Spotlight } from "@/app/_components/landing/Spotlight";
import { HowItWorks } from "@/app/_components/landing/HowItWorks";
import { Ethos } from "@/app/_components/landing/Ethos";
import { Faq } from "@/app/_components/landing/Faq";
import { FinalCta } from "@/app/_components/landing/FinalCta";
import { Footer } from "@/app/_components/landing/Footer";

/**
 * Public landing page. Owns a 100dvh vertical scroll container because the root
 * <body> is locked to 100dvh/overflow:hidden by the workspace AppShell - so this
 * page must scroll its own viewport. Sections are composed from collocated
 * components under app/_components/landing/.
 */
export default function LandingPage() {
  return (
    <ScrollViewport variant="viewport" style={{ scrollBehavior: "smooth" }}>
      <LandingNav />
      <main>
        <Hero />
        <SourceOrbit />
        <LogoStrip />
        <SpeedStats />
        <Box id="how" style={{ scrollMarginTop: 100 }}>
          <HowItWorks />
        </Box>
        <Personas />
        <Marquee />
        <Capabilities />
        <Spotlight />
        <Box id="ethos" style={{ scrollMarginTop: 100 }}>
          <Ethos />
        </Box>
        <Box id="faq" style={{ scrollMarginTop: 100 }}>
          <Faq />
        </Box>
        <FinalCta />
      </main>
      <Footer />
    </ScrollViewport>
  );
}
