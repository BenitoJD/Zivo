#!/usr/bin/env python3
"""E2E probe: real PDF page → triage → IWF quality-gated MCQ generation.

Usage (from backend/):
  python scripts/e2e_mcq_quality_probe.py [--aspects N]

Prints pass/reject rates and per-question attempt counts. Cleans up test data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

import fitz

# Ensure backend package is importable when run as script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.eta.handlers.cpu import ingest_page_job  # noqa: E402
from app.graphs.page_triage_graph import run_page_triage  # noqa: E402
from app.models import Document  # noqa: E402
from app.services.mcq_quality import generate_quality_mcq  # noqa: E402
from app.services.retrieval import fetch_chunks_for_page_range  # noqa: E402
from app.services.storage import save_upload  # noqa: E402

PAGE_TEXT = """
Photosynthesis

Photosynthesis is the process by which green plants, algae, and some bacteria convert light energy
into chemical energy stored in glucose. It occurs mainly in chloroplasts, which contain the pigment
chlorophyll that absorbs red and blue wavelengths of visible light while reflecting green.

The overall reaction can be summarized as: 6 CO2 + 6 H2O + light energy → C6H12O6 + 6 O2.
The process has two major stages. The light-dependent reactions take place in the thylakoid membranes.
They produce ATP and NADPH and release oxygen as a by-product when water molecules are split.
The Calvin cycle (light-independent reactions) occurs in the stroma and uses ATP and NADPH to fix
carbon dioxide into organic sugars.

Three factors that limit the rate of photosynthesis are light intensity, carbon dioxide concentration,
and temperature. Each factor can become limiting when the others are not in short supply.
Stomata on leaf surfaces regulate gas exchange, allowing CO2 to enter while controlling water loss.
"""


def _make_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), PAGE_TEXT.strip(), fontsize=11, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def _cleanup(db, doc_id: uuid.UUID) -> None:
    from sqlalchemy import text

    db.execute(
        text("DELETE FROM intel.assertion WHERE payload->>'artifact_id' = :aid"),
        {"aid": str(doc_id)},
    )
    db.execute(text("DELETE FROM document_chunks WHERE document_id = :id"), {"id": str(doc_id)})
    doc = db.get(Document, doc_id)
    if doc:
        db.delete(doc)
    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aspects", type=int, default=5, help="Max aspects to generate (default 5)")
    args = parser.parse_args()

    pdf_bytes = _make_pdf()
    doc_id = uuid.uuid4()
    storage_key = save_upload(None, f"e2e-quality-{doc_id}.pdf", pdf_bytes, "application/pdf")

    db = SessionLocal()
    try:
        doc = Document(
            id=doc_id,
            slug=f"e2e-quality-{doc_id.hex[:8]}",
            filename="photosynthesis.pdf",
            content_type="application/pdf",
            size_bytes=len(pdf_bytes),
            storage_key=storage_key,
            status="ready",
            meta={
                "guest_id": "e2e" + "0" * 29,
                "page_count": 1,
                "selected_range": {"from": 1, "to": 1, "pages": [1]},
            },
        )
        db.add(doc)
        db.commit()

        print("Indexing page 1…")
        ingest_result = ingest_page_job({"document_id": str(doc_id), "page_number": 1})
        print(f"  chunks indexed: {ingest_result.get('chunks', 0)}")

        print("Running page triage…")
        triage = asyncio.run(run_page_triage(db, doc_id, page_number=1))
        aspects = triage.get("aspects") or []
        budget = triage.get("question_budget", len(aspects))
        print(f"  question_budget={budget}, aspects={len(aspects)}")
        if triage.get("aspect_dedup"):
            dedup = triage["aspect_dedup"]
            print(f"  aspect dedup: {dedup.get('deduped_count')} unique ({dedup.get('raw_count')} raw)")
        if triage.get("rationale"):
            print(f"  rationale: {triage['rationale']}")

        chunks = fetch_chunks_for_page_range(db, document_ids=[doc_id], page_start=1, page_end=1)
        page_text = "\n\n".join(c["text"] for c in chunks if c.get("text")).strip()
        if not page_text:
            print("ERROR: no page text after ingest", file=sys.stderr)
            return 1

        max_n = min(args.aspects, len(aspects))
        results: list[dict] = []
        prior_mcqs: list[dict] = []

        print(f"\nGenerating {max_n} quality-gated MCQs (real LLM + critic + dedup)…\n")
        for i, aspect in enumerate(aspects[:max_n], start=1):
            label = aspect.get("label") or aspect.get("key")
            print(f"[{i}/{max_n}] aspect: {label}")
            payload = asyncio.run(
                generate_quality_mcq(
                    db,
                    page_text=page_text,
                    page_number=1,
                    sequence=i,
                    target_aspect=aspect,
                    prior_mcqs=prior_mcqs,
                )
            )
            if payload is None:
                print("  → REJECTED (exhausted 3 attempts)")
                results.append({"aspect": label, "status": "rejected", "attempts": 3})
            else:
                q = payload.get("quality") or {}
                attempts = q.get("attempts", "?")
                print(f"  → PASSED on attempt {attempts}")
                print(f"     cognitive_level={q.get('cognitive_level')}, flaw_count={q.get('flaw_count')}")
                if q.get("max_similarity_to_prior") is not None:
                    print(f"     max_similarity_to_prior={q.get('max_similarity_to_prior')}")
                print(f"     Q: {(payload.get('question') or '')[:90]}…")
                results.append(
                    {
                        "aspect": label,
                        "status": "passed",
                        "attempts": attempts,
                        "cognitive_level": q.get("cognitive_level"),
                        "flaw_count": q.get("flaw_count"),
                        "provokes_understanding": q.get("provokes_understanding"),
                        "max_similarity_to_prior": q.get("max_similarity_to_prior"),
                    }
                )
                from app.services.mcq_dedup import prior_mcq_from_payload

                prior_mcqs.append(prior_mcq_from_payload(payload))

        passed = [r for r in results if r["status"] == "passed"]
        rejected = [r for r in results if r["status"] == "rejected"]
        first_try = [r for r in passed if r.get("attempts") == 1]

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"  Total attempted:  {len(results)}")
        print(f"  Passed:           {len(passed)} ({100 * len(passed) / max(1, len(results)):.0f}%)")
        print(f"  Rejected:         {len(rejected)} ({100 * len(rejected) / max(1, len(results)):.0f}%)")
        print(f"  First-try pass:   {len(first_try)} ({100 * len(first_try) / max(1, len(results)):.0f}%)")
        if passed:
            avg_attempts = sum(int(r.get("attempts") or 1) for r in passed) / len(passed)
            print(f"  Avg attempts (passed): {avg_attempts:.1f}")
        print("\nDetails:")
        print(json.dumps(results, indent=2))

        _cleanup(db, doc_id)
        print("\nTest document cleaned up.")
        return 0 if passed else 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        try:
            _cleanup(db, doc_id)
        except Exception:
            pass
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
