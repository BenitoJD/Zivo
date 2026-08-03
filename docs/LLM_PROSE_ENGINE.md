# LLM Prose Engine

**Status:** wired at the LiteLLM router boundary (`llm_router.py`).
**Owns the question:** is this LLM output safe to show in product copy (no em/en dash glyphs)?
**Product home:** every generated question, feedback, chat token, lesson, SEO article, coach blurb.

Related: [ENGINES.md](ENGINES.md), [ADR 0004](adr/0004-swappable-policy-seam.md), `.cursor/rules/no-em-dashes.mdc`.

---

## API

### Input
Raw model text (full completion or streaming chunk).

### Output
```
LlmProseVerdict { text, replacements, policy, policy_version }
```

Version field: `qb.llm_prose.v1`.

### Verbatim opt-out
`verbatim=True` skips normalization (e.g. mains OCR must preserve the learner's punctuation).

---

## Policy seam (ADR 0004)

Callers depend on `app.services.llm_prose_engine.sanitize_llm_output` only at the
router boundary. Do not sprinkle dash-stripping in graphs, API routes, or the frontend.

Live path: `acomplete_chat`, `complete_chat`, `stream_chat_completion` in `llm_router.py`.

---

## What not to do

- Do not call LiteLLM outside `llm_router` for product text.
- Do not strip dashes from user-authored uploads or OCR transcriptions (`verbatim=True`).
- Do not fork policy to the offline client — packs are built server-side through this seam.
