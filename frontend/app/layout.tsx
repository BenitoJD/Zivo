import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import Script from "next/script";
import { Figtree, EB_Garamond } from "next/font/google";
import "@mantine/core/styles.css";
import "@mantine/dropzone/styles.css";
import "@mantine/notifications/styles.css";
import Providers from "@/app/providers";
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
  title: "Zivo — Question Better.",
  description: "Upload anything. Get exam-style questions, scoped exactly to what you read, with a tutor that knows your source.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const cookieStore = await cookies();
  const colorScheme = readColorSchemeFromCookie(
    cookieStore.get(MANTINE_COLOR_SCHEME_COOKIE)?.value,
  );

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
        <Script id="unregister-stale-sw" strategy="beforeInteractive">
          {`if("serviceWorker" in navigator){navigator.serviceWorker.getRegistrations().then((rs)=>{rs.forEach((r)=>r.unregister())})}`}
        </Script>
        <style
          dangerouslySetInnerHTML={{
            __html: `
              html, body, input, button, select, textarea {
                font-family: var(--font-sans), 'Figtree', -apple-system, BlinkMacSystemFont, sans-serif !important;
              }
              h1, h2, h3, h4, h5, h6, .serif-text {
                font-family: var(--font-serif), 'EB Garamond', Georgia, serif !important;
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
        <Providers colorScheme={colorScheme}>{children}</Providers>
      </body>
    </html>
  );
}
