"""Render a generated quiz to a .docx worksheet (student or teacher/answer-key version)."""

from __future__ import annotations

import io

_LETTERS = "ABCDEFGH"


def _answer_text(q: dict) -> str:
    t = q.get("type")
    if t == "mcq":
        i = q.get("answer_index", 0)
        opts = q.get("options", [])
        return f"{_LETTERS[i]}. {opts[i]}" if i < len(opts) else _LETTERS[i]
    if t == "multi":
        idxs = q.get("answer_indices", [])
        opts = q.get("options", [])
        return ", ".join(f"{_LETTERS[i]}. {opts[i]}" for i in idxs if i < len(opts))
    if t == "truefalse":
        return "True" if q.get("answer") else "False"
    if t in ("fill_blank", "short", "essay"):
        return str(q.get("answer") or "")
    if t == "matching":
        return "; ".join(f"{p.get('left')} → {p.get('right')}" for p in q.get("pairs", []))
    return ""


def build_quiz_docx(*, title: str, questions: list[dict], with_answers: bool) -> bytes:
    """Build a worksheet docx. with_answers=True produces the teacher/answer-key version."""
    from docx import Document as Docx
    from docx.shared import Pt

    doc = Docx()
    doc.add_heading(title or "Quiz", level=0)
    sub = doc.add_paragraph("Answer key" if with_answers else "Name: ____________________    Date: __________")
    sub.runs[0].italic = True

    for n, q in enumerate(questions, 1):
        p = doc.add_paragraph()
        run = p.add_run(f"{n}. {q.get('prompt', '')}")
        run.bold = True
        run.font.size = Pt(11)

        t = q.get("type")
        if t in ("mcq", "multi"):
            for i, opt in enumerate(q.get("options", [])):
                doc.add_paragraph(f"{_LETTERS[i]}. {opt}", style="List Bullet")
        elif t == "truefalse":
            doc.add_paragraph("True  /  False")
        elif t == "matching":
            lefts = [p_.get("left", "") for p_ in q.get("pairs", [])]
            rights = [p_.get("right", "") for p_ in q.get("pairs", [])]
            # Shuffle-free: present columns; the answer key lists the correct pairing.
            for i, left in enumerate(lefts):
                doc.add_paragraph(f"{i + 1}. {left}    ____", style="List Bullet")
            for j, right in enumerate(rights):
                doc.add_paragraph(f"   {_LETTERS[j]}. {right}")
        elif t in ("fill_blank", "short"):
            doc.add_paragraph("__________________________________________")
        elif t == "essay":
            for _ in range(4):
                doc.add_paragraph("__________________________________________")

        if with_answers:
            ap = doc.add_paragraph()
            ar = ap.add_run(f"Answer: {_answer_text(q)}")
            ar.italic = True
            expl = (q.get("explanation") or "").strip()
            if expl:
                ep = doc.add_paragraph()
                er = ep.add_run(expl)
                er.italic = True
                er.font.size = Pt(9)
        doc.add_paragraph("")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
