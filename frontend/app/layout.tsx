import type { Metadata, Viewport } from "next";
import { Plus_Jakarta_Sans, Newsreader } from "next/font/google";
import "@mantine/core/styles.css";
import "@mantine/dropzone/styles.css";
import "@mantine/notifications/styles.css";
import Providers from "@/app/providers";
import { MANTINE_COLOR_SCHEME_SCRIPT, mantineHtmlProps } from "@/lib/mantine-color-scheme";

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
  title: "ZIVO",
  description: "Measure and improve understanding through questions.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" {...mantineHtmlProps}>
      <head>
        <script
          data-mantine-script
          dangerouslySetInnerHTML={{ __html: MANTINE_COLOR_SCHEME_SCRIPT }}
        />
      </head>
      <body
        className={`${plusJakartaSans.variable} ${newsreader.variable}`}
        style={{ height: "100dvh", overflow: "hidden", margin: 0 }}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
