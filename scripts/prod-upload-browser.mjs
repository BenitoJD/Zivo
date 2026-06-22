import { chromium } from "playwright";
import fs from "fs";
import os from "os";
import path from "path";

const MIN_PDF = Buffer.from(
  "%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n" +
    "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" +
    "3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n" +
    "xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n" +
    "0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n",
);

(async () => {
  const failed = [];
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  page.on("requestfailed", (req) => {
    if (req.url().includes("api.zivo.fyi")) {
      failed.push(`${req.method()} ${req.url()} — ${req.failure()?.errorText}`);
    }
  });

  const tmpPdf = path.join(os.tmpdir(), `zivo-prod-${Date.now()}.pdf`);
  fs.writeFileSync(tmpPdf, MIN_PDF);

  try {
    await page.goto("https://zivo.fyi/workspace", { waitUntil: "networkidle", timeout: 60_000 });
    await page.getByRole("button", { name: "Add source" }).click();
    const dialog = page.getByRole("dialog", { name: "Add to library" });
    await dialog.waitFor({ state: "visible", timeout: 10_000 });
    await dialog.locator('input[type="file"]').setInputFiles(tmpPdf);

    await page.waitForURL(/\/workspace\/[0-9a-f-]{36}/, { timeout: 30_000 });
    const body = await page.content();
    if (body.includes("Failed to fetch")) {
      throw new Error("Page shows 'Failed to fetch'");
    }
    if (failed.length) {
      throw new Error(`API failures: ${failed.join("; ")}`);
    }
    console.log("OK  Browser upload navigated to", page.url());
    console.log("PASS production browser upload smoke");
  } finally {
    fs.unlinkSync(tmpPdf);
    await browser.close();
  }
})().catch((e) => {
  console.error("FAIL", e.message);
  process.exit(1);
});
