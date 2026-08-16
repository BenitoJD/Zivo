"""Render a structured resume to a .docx file — ATS-friendly or a lightly-styled 'modern' layout.

The ATS template is deliberately plain single-column (no tables/columns/graphics) so applicant
tracking systems parse it cleanly; 'modern' adds a coloured name + rules but stays single-column
(still ATS-safe, just prettier). Structured data matches resume.py's `structured` shape.
"""

from __future__ import annotations

import io
from typing import Any

from app.engine_runtime import pick


def build_resume_docx(data: dict[str, Any], *, template: str = "ats") -> bytes:
    """Build a resume .docx from structured data. template: 'ats' (plain) or 'modern'."""
    from docx import Document as Docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    modern = template == "modern"
    doc = Docx()

    name = doc.add_heading(data.get("name") or "Your Name", level=0)
    pick(
        modern,
        lambda: (
            setattr(name, "alignment", WD_ALIGN_PARAGRAPH.CENTER),
            [setattr(run.font.color, "rgb", RGBColor(0x5A, 0x4B, 0xD6)) for run in name.runs],
        ),
        lambda: None,
    )
    pick(
        bool(data.get("title")),
        lambda: _add_title(doc, data["title"], modern, WD_ALIGN_PARAGRAPH),
        lambda: None,
    )

    contact = doc.add_paragraph()
    contact_parts = [data.get("email"), data.get("phone"), data.get("location"), *(data.get("links") or [])]
    contact.add_run("  |  ".join(filter(None, contact_parts)))
    pick(modern, lambda: setattr(contact, "alignment", WD_ALIGN_PARAGRAPH.CENTER), lambda: None)

    def section(title: str) -> None:
        h = doc.add_heading(title, level=1)
        pick(
            modern,
            lambda: [setattr(run.font.color, "rgb", RGBColor(0x5A, 0x4B, 0xD6)) for run in h.runs],
            lambda: None,
        )

    pick(
        bool(data.get("summary")),
        lambda: (section("Summary"), doc.add_paragraph(data["summary"])),
        lambda: None,
    )

    experience = data.get("experience") or []
    pick(bool(experience), lambda: _experience(doc, experience, section), lambda: None)

    education = data.get("education") or []
    pick(bool(education), lambda: _education(doc, education, section), lambda: None)

    skills = data.get("skills") or []
    pick(
        bool(skills),
        lambda: (section("Skills"), doc.add_paragraph(", ".join(str(s) for s in skills))),
        lambda: None,
    )

    style = doc.styles["Normal"]
    style.font.size = Pt(10.5)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _add_title(doc, title, modern, align) -> None:
    t = doc.add_paragraph(title)
    pick(modern, lambda: setattr(t, "alignment", align.CENTER), lambda: None)


def _experience(doc, experience, section) -> None:
    section("Experience")
    for job in experience:
        p = doc.add_paragraph()
        p.add_run(f"{job.get('role','')}".strip()).bold = True
        company = job.get("company", "")
        dates = job.get("dates", "")
        meta = " — ".join(filter(None, [company, dates]))
        pick(bool(meta), lambda: p.add_run(f"   {meta}"), lambda: None)
        for b in (job.get("bullets") or []):
            doc.add_paragraph(str(b), style="List Bullet")


def _education(doc, education, section) -> None:
    section("Education")
    for ed in education:
        line = " — ".join(filter(None, [ed.get("degree", ""), ed.get("school", ""), ed.get("dates", "")]))
        doc.add_paragraph(line)


def _self_check() -> None:
    sample = {
        "name": "Jane Doe", "title": "Backend Engineer", "email": "jane@example.com",
        "phone": "+1 415 555 1234", "location": "SF", "links": ["github.com/jane"],
        "summary": "Backend engineer with 5 years building scalable services.",
        "experience": [{"role": "Senior Engineer", "company": "Acme", "dates": "2021–now",
                        "bullets": ["Led latency cut of 40%", "Shipped 3 services"]}],
        "education": [{"degree": "BS CS", "school": "MIT", "dates": "2016"}],
        "skills": ["Python", "Go", "SQL"],
    }
    for tmpl in ("ats", "modern"):
        out = build_resume_docx(sample, template=tmpl)
        assert out[:2] == b"PK" and len(out) > 1000, tmpl
    print("resume_export self-check OK")


pick(__name__ == "__main__", _self_check, lambda: None)
