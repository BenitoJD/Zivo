import type { Metadata, Viewport } from "next";
import { cookies, headers } from "next/headers";
import { Figtree, EB_Garamond } from "next/font/google";
import "@mantine/core/styles.css";
import "@mantine/dropzone/styles.css";
import "@mantine/notifications/styles.css";
import "@/app/notifications-apple.css";
import "@/app/responsive-scale.css";
import "@/app/scrollbar.css";
import Providers from "@/app/providers";
import RegisterServiceWorker from "@/app/_components/RegisterServiceWorker";
import {
  MANTINE_COLOR_SCHEME_COOKIE,
  MANTINE_COLOR_SCHEME_SCRIPT,
  readColorSchemeFromCookie,
} from "@/lib/mantine-color-scheme";

const figtree = Figtree({
  subsets: ["latin"],
  variable: "--font-sans",
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const ebGaramond = EB_Garamond({
  subsets: ["latin"],
  variable: "--font-serif",
  weight: ["400", "500", "600", "700"],
  style: ["normal", "italic"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Zivo | Question Better.",
  description: "Upload anything. Get exam-style questions, scoped exactly to what you read, with a tutor that knows your source.",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "Zivo", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  // Soft keyboards (iOS/Android) resize the visual viewport so study composers stay in view.
  interactiveWidget: "resizes-content",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const cookieStore = await cookies();
  const headerStore = await headers();
  const forceLight = headerStore.get("x-zivo-force-light") === "1";
  const colorScheme = forceLight
    ? "light"
    : readColorSchemeFromCookie(cookieStore.get(MANTINE_COLOR_SCHEME_COOKIE)?.value);

  return (
    <html
      lang="en"
      className={`${figtree.variable} ${ebGaramond.variable}`}
      data-mantine-color-scheme={colorScheme}
      suppressHydrationWarning
    >
      <head>
        <script
          data-mantine-script
          dangerouslySetInnerHTML={{ __html: MANTINE_COLOR_SCHEME_SCRIPT }}
        />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        {/* eslint-disable-next-line @next/next/no-page-custom-font -- Google Fonts via <link>+preconnect in the App Router head is intentional; next/font would conflict with the Calm Paper CSS font variables */}
        <link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400..700;1,400..700&family=Newsreader:ital,opsz,wght@0,6..72,200..800;1,6..72,200..800&display=swap" rel="stylesheet" />
        <style
          dangerouslySetInnerHTML={{
            __html: `
              html, body, input, button, select, textarea {
                font-family: var(--font-sans), 'Figtree', -apple-system, BlinkMacSystemFont, sans-serif !important;
              }
              /* Wispr-style: elegant serif display headings over a sans body. */
              h1, h2, h3, h4, h5, h6 {
                font-family: 'Newsreader', 'EB Garamond', Georgia, serif !important;
                letter-spacing: -0.01em;
              }
              /* Escape hatch for anything that must stay sans inside a heading. */
              .sans-text {
                font-family: var(--font-sans), 'Figtree', sans-serif !important;
              }
              /* Premium warm/ink text selection */
              ::selection {
                background: var(--mantine-color-lavender-1);
                color: var(--mantine-color-lavender-9);
              }
              /* Custom scrollbars - slim, calm, and inset so the thumb floats as a
                 soft pill instead of a hard edge-to-edge bar (Calm Paper). */
              * {
                scrollbar-width: thin;
                scrollbar-color: var(--mantine-color-gray-4) transparent;
              }
              ::-webkit-scrollbar {
                width: 11px;
                height: 11px;
              }
              ::-webkit-scrollbar-track {
                background: transparent;
              }
              ::-webkit-scrollbar-thumb {
                background: var(--mantine-color-gray-4);
                border-radius: 999px;
                border: 3px solid transparent;
                background-clip: padding-box;
                transition: background 160ms ease;
              }
              ::-webkit-scrollbar-thumb:hover {
                background: var(--mantine-color-gray-5);
              }
              ::-webkit-scrollbar-corner { background: transparent; }
              /* Ambient background light glows */
              .spotlight-glow {
                position: absolute;
                inset: 0;
                pointer-events: none;
                z-index: 0;
                background: radial-gradient(circle 800px at 50% -200px, rgba(123, 93, 166, 0.06), transparent 80%);
              }
              [data-mantine-color-scheme="dark"] .spotlight-glow {
                background: radial-gradient(circle 800px at 50% -200px, rgba(179, 161, 204, 0.03), transparent 80%);
              }
              /* Shimmer loader style */
              @keyframes paper-shimmer {
                0% { background-position: -200% 0; }
                100% { background-position: 200% 0; }
              }
              .shimmer-bg {
                background: linear-gradient(90deg, var(--mantine-color-gray-0) 25%, var(--mantine-color-gray-2) 50%, var(--mantine-color-gray-0) 75%);
                background-size: 200% 100%;
                animation: paper-shimmer 1.8s infinite linear;
              }
              /* Landing reveal - one-shot fade-up on mount (see _components/landing/motion.tsx). */
              @keyframes zivo-reveal-in {
                from { opacity: 0; transform: translateY(20px); }
                to { opacity: 1; transform: none; }
              }
              .zivo-reveal {
                animation: zivo-reveal-in 600ms cubic-bezier(0.32, 0.72, 0, 1) both;
              }
              @media (prefers-reduced-motion: reduce) {
                .zivo-reveal { animation: none !important; }
              }
            `,
          }}
        />
      </head>
      <body
        style={{
          height: "100dvh",
          overflow: "hidden",
          margin: 0,
          WebkitFontSmoothing: "antialiased",
          MozOsxFontSmoothing: "grayscale",
        }}
      >
        <Providers colorScheme={colorScheme}>
          <RegisterServiceWorker />
          {children}
        </Providers>
      </body>
    </html>
  );
}
