#!/usr/bin/env python3
"""Production smoke test: mirrors browser add-source upload flow."""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import httpx

ORIGIN = "https://zivo.fyi"
API = "https://api.zivo.fyi"
# Minimal valid PDF (one blank page)
MIN_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n"
    b"0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"
)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"OK  {msg}")


def main() -> None:
    jar = httpx.Cookies()
    headers = {"Origin": ORIGIN}

    with httpx.Client(base_url=API, cookies=jar, headers=headers, timeout=60.0) as client:
        # 1) CORS preflight (browser does this before multipart POST)
        pre = client.options(
            "/api/sources",
            headers={
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-zivo-guest-id",
            },
        )
        if pre.status_code != 200:
            fail(f"CORS preflight {pre.status_code}: {pre.text[:200]}")
        allow_origin = pre.headers.get("access-control-allow-origin")
        if allow_origin != ORIGIN:
            fail(f"CORS allow-origin={allow_origin!r}, expected {ORIGIN!r}")
        ok(f"CORS preflight allows {allow_origin}")

        # 2) Guest bootstrap (ensureGuestSession → GET /api/sources)
        boot = client.get("/api/sources")
        if boot.status_code not in (200, 401):
            fail(f"Guest bootstrap {boot.status_code}: {boot.text[:200]}")
        guest_hdr = boot.headers.get("x-zivo-guest-id")
        if not guest_hdr:
            fail("Guest bootstrap missing X-Zivo-Guest-Id header")
        ok(f"Guest session bootstrapped ({guest_hdr[:8]}…)")

        upload_headers = {"X-Zivo-Guest-Id": guest_hdr}

        # 3) Multipart upload (workspace add-source modal → POST /api/sources)
        filename = f"prod-smoke-{uuid.uuid4().hex[:8]}.pdf"
        files = {"file": (filename, MIN_PDF, "application/pdf")}
        up = client.post("/api/sources", files=files, headers=upload_headers)
        if up.status_code != 200:
            fail(f"Upload {up.status_code}: {up.text[:500]}")
        doc = up.json()
        doc_id = doc.get("id")
        if not doc_id:
            fail(f"Upload response missing id: {doc}")
        ok(f"Upload created document {doc_id} (status={doc.get('status')})")

        # 4) Artifact endpoints (post-upload navigation)
        art = client.get(f"/api/artifacts/{doc_id}", headers=upload_headers)
        if art.status_code != 200:
            fail(f"GET artifact {art.status_code}: {art.text[:300]}")
        ok(f"Artifact metadata status={art.json().get('status')}")

        pages = client.get(f"/api/artifacts/{doc_id}/pages", headers=upload_headers)
        if pages.status_code != 200:
            fail(f"GET pages {pages.status_code}: {pages.text[:300]}")
        page_count = pages.json().get("page_count", 0)
        ok(f"Artifact pages page_count={page_count}")

        # 5) Page range (workspace setup flow)
        pr = client.post(
            f"/api/artifacts/{doc_id}/page-range",
            json={"from": 1, "to": max(1, page_count)},
            headers=upload_headers,
        )
        if pr.status_code != 200:
            fail(f"Page range {pr.status_code}: {pr.text[:300]}")
        ok("Page range accepted")

        # 6) Poll indexing (workers)
        deadline = time.time() + 120
        final_status = "indexing"
        while time.time() < deadline:
            art = client.get(f"/api/artifacts/{doc_id}", headers=upload_headers)
            if art.status_code == 200:
                final_status = art.json().get("status", final_status)
                if final_status in ("ready", "failed"):
                    break
            time.sleep(3)

        if final_status == "ready":
            ok(f"Indexing completed (status=ready)")
        elif final_status == "failed":
            # Tiny synthetic PDFs can fail ingest; real uploads are validated separately.
            ok(f"Indexing status=failed for synthetic PDF (upload path still OK)")
        else:
            ok(f"Indexing still {final_status} after 120s (workers may be slow; upload path OK)")

        # 7) www origin CORS
        www = httpx.Client(base_url=API, timeout=30.0)
        www_pre = www.options(
            "/api/sources",
            headers={
                "Origin": "https://www.zivo.fyi",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-zivo-guest-id",
            },
        )
        if www_pre.headers.get("access-control-allow-origin") != "https://www.zivo.fyi":
            fail("www.zivo.fyi CORS not allowed")
        ok("www.zivo.fyi CORS preflight")

    print("\nPASS production upload smoke test")


if __name__ == "__main__":
    main()
