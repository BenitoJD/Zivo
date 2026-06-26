# Question Better. — vision and roadmap

This document captures where we are going, what we build when, and why it is hard to copy.

## Destination (one sentence)

**Become the world's authority on measuring understanding through questions.**

Not MCQs. Not exams. Not education as a category. **Understanding.** Questions are the instrument.

## North star

> Become the company that understands questions better than anyone on Earth.

When we succeed, "Question Better." stops being a tagline. It becomes a description of the company.

## The unchanging core (the 100-year law)

> **Build the unchanging loop that always finds the best question for whoever shows up — and never let the format, the model, or the metric into the part you call permanent.**

A frozen system cannot stay the best — the world moves underneath it. The only things that stay supreme for a century are **self-improving loops**, not artifacts: the scientific method, double-entry bookkeeping, the Socratic method, evolution. None is a thing; each is a process that changes its every output while its core stays fixed. We are not the pyramid of questions. We are the scientific method of questions.

So we freeze the **law and the loop**, and keep everything else liquid.

**What is permanent (the laws):**

1. **Purpose** — a question exists to change the person answering it. Value = understanding gained.
2. **Relativity** — "best" is always relative to one learner at their edge. There is no best question in the abstract.
3. **Revelation** — understanding is known only through response. The system listens; it never assumes.
4. **The loop** — ask → observe the response → choose the next prompt that maximizes understanding → repeat, getting better at the choosing.
5. **Simplicity** — the prompt is as simple as it can be and no simpler. Difficulty lives in the thinking, not the wording.
6. **Goal fixed, method open** — the goal (maximize understanding) never changes; *how* we reach it is always learned and replaced.

**What must stay swappable (era-guesses — never in the foundation):**

- **The format.** The MCQ is today's vessel for "a prompt that forces a revealing response." Store the function; render the format. In 100 years the format will be unrecognizable.
- **The model and vendor.** Always behind an abstraction. A config row, never an identity.
- **The metric.** A frozen definition of "good question" invites Goodhart. The evaluator must be learned from outcomes, not carved in stone.

**The one substrate-independent asset to accumulate:** the record of how real humans responded. Questions age, code rots, models get replaced — but "when a human at this level of understanding met this idea, here is what they revealed" is as valid in 2125 as today. That signal is the soil the loop grows in.

**The decision test:** for any feature, schema, or dependency — *is this an expression of a law, or a guess about the current era?* Laws are permanent and protected. Era-guesses are trivially replaceable, and our identity never depends on them.

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
