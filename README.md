# Question Better.

**[zivo.fyi](https://zivo.fyi)**

We measure and improve understanding through questions.

MCQs are not the product. They are the engine. Our job is to help people learn faster, remember more, and score higher — by building the best questions in the world, and learning from every answer.

## Mission

Measure and improve understanding through questions.

## Vision

Become the world's platform for understanding, evaluation, and knowledge verification.

## What we believe

- **Questions are the instrument.** Understanding is the outcome.
- **Quality beats quantity.** Anyone can generate MCQs with AI. Few can tell which questions actually measure mastery.
- **Signals are the moat.** A million static questions is a commodity. Billions of answer interactions — difficulty, misconceptions, what predicts success — is not.
- **MCQs are not going away.** Schools, certifications, and employers still need fast, fair, scalable evaluation. The value shifts from *owning questions* to *helping people succeed through them*.

## What we are building

```
Learn → Practice → Identify weaknesses → Improve → Pass
```

Under the hood:

| Layer | Purpose |
|-------|---------|
| **Question engine** | Turn any source (PDF, transcript, course, notes) into high-quality questions |
| **Question evaluator** | Score ambiguity, difficulty, recall vs reasoning, usefulness |
| **Question graph** | Connect questions to concepts, prerequisites, and each other |
| **Answer intelligence** | Learn from every response what works, what fails, what predicts mastery |

Year 1 is deliberately narrow: **become the best question generator.** One product. Nothing else.

Full strategy and 10-year roadmap: [docs/VISION.md](docs/VISION.md).

## What we are not building (yet)

- Question marketplace
- Social network or rankings-first product
- Hiring platform, certification body, or generic LMS
- "Largest MCQ database" as the pitch

Those can come later. First we earn the right to own **Question Better.**

## For developers

| | |
|---|---|
| **Repo** | [github.com/BenitoJD/Zivo](https://github.com/BenitoJD/Zivo) — `Zivo` is the engineering codename; **Question Better.** is the product brand |
| **Stack** | FastAPI · Next.js · Postgres · MinIO · K3s |
| **Quick start** | `./scripts/dev.sh setup && ./scripts/dev.sh start` → API at http://127.0.0.1:8200 |
| **Frontend** | `cd frontend && npm install && npm run dev` → http://localhost:3000 |
| **Agent guide** | [AGENTS.md](AGENTS.md) |
| **Infrastructure** | [infra/k8s/README.md](infra/k8s/README.md) |

```bash
curl http://127.0.0.1:8200/health
```
