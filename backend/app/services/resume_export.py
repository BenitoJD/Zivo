"""Render a structured resume to a .docx file — ATS-friendly or a lightly-styled 'modern' layout.

The ATS template is deliberately plain single-column (no tables/columns/graphics) so applicant
tracking systems parse it cleanly; 'modern' adds a coloured name + rules but stays single-column
(still ATS-safe, just prettier). Structured data matches resume.py's `structured` shape.
"""

from __future__ import annotations

import io
from typing import Any


def build_resume_docx(data: dict[str, Any], *, template: str = "ats") -> bytes:
    """Build a resume .docx from structured data. template: 'ats' (plain) or 'modern'."""
    from docx import Document as Docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    modern = template == "modern"
    doc = Docx()

    name = doc.add_heading(data.get("name") or "Your Name", level=0)
    if modern:
        name.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in name.runs:
            run.font.color.rgb = RGBColor(0x5A, 0x4B, 0xD6)  # lavender ink
    if data.get("title"):
        t = doc.add_paragraph(data["title"])
        if modern:
            t.alignment = WD_ALIGN_PARAGRAPH.CENTER

    contact = doc.add_paragraph()
    contact_parts = [data.get("email"), data.get("phone"), data.get("location"), *(data.get("links") or [])]
    contact.add_run("  |  ".join(p for p in contact_parts if p))
    if modern:
        contact.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def section(title: str) -> None:
        h = doc.add_heading(title, level=1)
        if modern:
            for run in h.runs:
                run.font.color.rgb = RGBColor(0x5A, 0x4B, 0xD6)

    if data.get("summary"):
        section("Summary")
        doc.add_paragraph(data["summary"])

    experience = data.get("experience") or []
    if experience:
        section("Experience")
        for job in experience:
            p = doc.add_paragraph()
            p.add_run(f"{job.get('role','')}".strip()).bold = True
            company = job.get("company", "")
            dates = job.get("dates", "")
            meta = " — ".join(x for x in [company, dates] if x)
            if meta:
                p.add_run(f"   {meta}")
            for b in (job.get("bullets") or []):
                doc.add_paragraph(str(b), style="List Bullet")

    education = data.get("education") or []
    if education:
        section("Education")
        for ed in education:
            line = " — ".join(x for x in [ed.get("degree", ""), ed.get("school", ""), ed.get("dates", "")] if x)
            doc.add_paragraph(line)

    skills = data.get("skills") or []
    if skills:
        section("Skills")
        doc.add_paragraph(", ".join(str(s) for s in skills))

    # Normalize base font size for compactness.
    style = doc.styles["Normal"]
    style.font.size = Pt(10.5)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


if __name__ == "__main__":  # pragma: no cover
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
        assert out[:2] == b"PK" and len(out) > 1000, tmpl  # valid .docx (zip) with content
    print("resume_export self-check OK")
