import type { Metadata, Viewport } from "next";
import "@mantine/core/styles.css";
import "@mantine/dropzone/styles.css";
import "@mantine/notifications/styles.css";
import Providers from "@/app/providers";
import { MANTINE_COLOR_SCHEME_SCRIPT, mantineHtmlProps } from "@/lib/mantine-color-scheme";

export const metadata: Metadata = {
  title: "Question Better.",
  description: "Measure and improve understanding through questions.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
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
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
