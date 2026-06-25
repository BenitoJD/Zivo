import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import Script from "next/script";
import { Plus_Jakarta_Sans, Newsreader } from "next/font/google";
import "@mantine/core/styles.css";
import "@mantine/dropzone/styles.css";
import "@mantine/notifications/styles.css";
import Providers from "@/app/providers";
import {
  MANTINE_COLOR_SCHEME_COOKIE,
  MANTINE_COLOR_SCHEME_SCRIPT,
  readColorSchemeFromCookie,
} from "@/lib/mantine-color-scheme";

const plusJakartaSans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const newsreader = Newsreader({
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
    <html lang="en" data-mantine-color-scheme={colorScheme} suppressHydrationWarning>
      <head>
        <script
          data-mantine-script
          dangerouslySetInnerHTML={{ __html: MANTINE_COLOR_SCHEME_SCRIPT }}
        />
        <Script id="unregister-stale-sw" strategy="beforeInteractive">
          {`if("serviceWorker" in navigator){navigator.serviceWorker.getRegistrations().then((rs)=>{rs.forEach((r)=>r.unregister())})}`}
        </Script>
      </head>
      <body
        className={`${plusJakartaSans.variable} ${newsreader.variable}`}
        style={{ height: "100dvh", overflow: "hidden", margin: 0 }}
      >
        <Providers colorScheme={colorScheme}>{children}</Providers>
      </body>
    </html>
  );
}
