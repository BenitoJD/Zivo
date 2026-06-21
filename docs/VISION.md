# Question Better. — vision and roadmap

This document captures where we are going, what we build when, and why it is hard to copy.

## Destination (one sentence)

**Become the world's authority on measuring understanding through questions.**

Not MCQs. Not exams. Not education as a category. **Understanding.** Questions are the instrument.

## North star

> Become the company that understands questions better than anyone on Earth.

When we succeed, "Question Better." stops being a tagline. It becomes a description of the company.

## Product principles

1. **One sharp wedge first.** Ship the best question generator before anything else.
2. **Obsess over question quality.** Useful? Ambiguous? Too easy? Measures recall or reasoning? Reveals mastery?
3. **Collect signals, not just content.** Every answer should make the system smarter.
4. **Build a graph, not a pile.** Questions know topic, difficulty, concept, prerequisites, success rate, common mistakes.
5. **Outcomes over formats.** People remember "I passed because of this," not "50,000 MCQs."

## The flywheel

```
Users answer questions
        ↓
We learn what works
        ↓
Questions improve
        ↓
Evaluations improve
        ↓
More users arrive
        ↓
We learn more
        ↓
Questions improve again
```

The moat is not the app. It is the loop.

---

## Phase 1 — Year 1: Best question generator

**Goal:** When someone uploads material, our questions are noticeably better than any alternative.

**Product**

- Input: PDF, YouTube transcript, website, notes, course outline
- Output: high-quality MCQs, explanations, difficulty estimates
- No marketplace, no social layer, no hiring — just generation done right

**Success metric**

People say: *"Their questions are insanely good."*

Not: *"Their app looks nice."*

**Copyable?** Yes — at this stage. Upload → generate MCQs is replicable in months. That is acceptable. Year 1 is about quality reputation and starting the signal loop.

---

## Phase 2 — Years 2–3: Best question evaluator

**Goal:** We grade the questions themselves — the Grammarly of assessment items.

**Capabilities**

- Detect ambiguity, trick wording, multiple correct answers
- Classify: recall vs comprehension vs reasoning
- Flag items that do not discriminate (everyone gets it right or wrong)
- Suggest rewrites and better distractors

**Success metric**

Authors and platforms trust our quality scores before publishing a question bank.

**Copyable?** Partially. Models can approximate evaluation. Harder to copy once evaluation is trained on millions of labeled items and outcomes.

---

## Phase 3 — Years 3–5: Question graph

**Goal:** Questions are nodes in a knowledge network, not rows in a spreadsheet.

**Each question knows**

- Concepts tested
- Prerequisites
- Difficulty (calibrated, not guessed)
- Related questions
- Common wrong answers and why
- Which follow-ups improve learning after a miss

**Example**

```
Question #15342
  Topics: algebra → factoring → polynomials
  Prerequisites: arithmetic, linear equations
  Difficulty: 7.2/10 (calibrated)
  Avg score: 61%
  Related: #1452, #1628, #2100
  Top misconception: confusing sign when expanding (a+b)²
```

**Success metric**

Adaptive study paths and weakness detection feel obviously better than flat quiz lists.

**Copyable?** No — not without equivalent graph structure and answer history.

---

## Phase 4 — Years 5–7: Assessment engine (Stripe for understanding)

**Goal:** Other products embed our engine instead of building their own.

**Customers**

- Edtech apps
- Corporate training
- Certification bodies
- Hiring and skills assessment
- AI labs evaluating model knowledge

**We sell**

- Question generation API
- Quality evaluation API
- Adaptive routing via the question graph
- Analytics: what predicts mastery, what does not

**Success metric**

Integrations choose us because the engine works — not because of dashboard polish.

**Copyable?** Very hard without the graph + behavioral dataset + calibration pipeline.

---

## Phase 5 — Years 7–10: Global understanding infrastructure

**Goal:** The largest dataset of how humans demonstrate understanding through questions.

**We can answer questions nobody else can**

- Which concepts are hardest globally?
- Which item types predict exam success?
- Which misconceptions are universal vs domain-specific?
- Which questions measure mastery vs lucky guessing?

**Success metric**

Default infrastructure for measurement — the way Stripe became default for payments.

---

## What creates the moat

| Asset | Defensibility |
|-------|-------------|
| Raw MCQ generation | Low — AI commoditizes this |
| Pretty UI | Low |
| Static question bank | Low — size alone is not a moat |
| Question quality models | Medium — improves with proprietary labels |
| **Question graph** | **High** — structure + calibration |
| **Answer interaction data** | **Very high** — billions of outcomes |
| **Understanding analytics** | **Very high** — unique research layer |

**Bad asset:** 10 million questions.

**Great asset:** 10 billion question–answer interactions with outcomes.

## What competitors can copy (honest)

| Timeframe | Copyable |
|-----------|----------|
| Year 1 | Yes — upload PDF, get MCQs |
| Years 2–3 | Partially — evaluation without our labeled data |
| Years 3+ | Increasingly no — graph + behavioral data compound |

Large AI labs can always build features. They struggle to replicate years of calibrated assessment signal tied to real outcomes.

## Horizontal expansion (after the engine works)

Same core powers multiple surfaces:

| Surface | Use case |
|---------|----------|
| Education | Students, exam prep, spaced repetition |
| Corporate | Employee training, compliance checks |
| Hiring | Skills assessment, role fit |
| Certification | Professional licensing exams |
| AI | Model evaluation, knowledge benchmarking |

Do not build all of these in Year 1. Build the engine once; expand when quality is undeniable.

## Current engineering focus

| Priority | Status |
|----------|--------|
| Infra (API, web, Postgres, K8s, CI/CD) | Ready |
| Question generation pipeline | **Next** |
| Question graph schema | Planned |
| Answer capture + analytics | Planned |
| Public API for third parties | Years 5+ |

## One line for the wall

Not: *Build an MCQ company.*

Not: *Build an exam company.*

**Become the company that understands questions better than anyone on Earth.**
