# Architecture Preferences

- Stack: FastAPI + SQLAlchemy (raw parameterized SQL on `intel.*`/`qb.*` Postgres+pgvector schemas), Next.js 16 App Router + Mantine 9, K3s+Helm on VPS. Confidence: 0.95
- Backend layering pattern: API route → service (business logic) → raw SQL repository. Reuses this pattern for every new feature. Confidence: 0.95
- Worker system: ETA workers with `JobWorkload.io` (async semaphore) and `JobWorkload.cpu` (ThreadPoolExecutor). Generation belongs in background workers, never inline on the request path. Confidence: 0.95
- Swappable-policy seam pattern: strategy interfaces behind configuration flags (e.g., `selection_policy` "sequence" vs "difficulty_edge"), degrade safely. Keep metrics swappable (Elo → IRT). Confidence: 0.9
- Alembic migrations for all schema changes, `references qb.documents(id) ON DELETE CASCADE`. Confidence: 0.95
- Postgres+pgvector for RAG via `document_chunks` table and `search_chunks`. Map-reduce LLM pattern for document-level generation. Confidence: 0.9
- Image hosting on MinIO, deployed as part of the K3s cluster. Confidence: 0.85
- Generation pipeline must stay quality-first: the critic/evaluator is the moat (per `docs/VISION.md`), never strip it for speed. But it belongs in background workers. Confidence: 0.95
- Generation should be moved off the critical user path entirely: request path is read-only (≤15ms), all generation happens in background workers. Confidence: 0.95
- Prefers per-page parallel guard (not per-document) for concurrent generation, with eager triage lookahead for next pages. Confidence: 0.9
- Design for open-world input: any document type, any topic, any format. Quality must not depend on knowing the input domain. Confidence: 0.9
- Model: MiMo V2.5 (310B/15B active MoE, 1M context, hosted API). Step 3.5 Flash was primary but MiMo is current default for generation. Confidence: 0.85
- Token caching awareness is important — wants prompt/semantic caching configured to reduce cost. Confidence: 0.9
- LLM router pattern: `complete_chat`/`acomplete_chat`/`stream_chat_completion`, tag-aware timeouts, failover retry (`num_retries` for 429 backoff), `litellm.drop_params=True`. Confidence: 0.9
- Per-mode chat conversations via a `surface` column rather than one global thread. Confidence: 0.85
- Prefers raw SQL over ORM for complex queries; avoids ORM magic that obscures what's happening. Confidence: 0.85
- Every answer should capture a measurement signal (correct, latency, chosen option) — the signal is the moat. Confidence: 0.9
- Question budget: AI triage estimates testable aspects per page, clamped [INITIAL_BATCH_SIZE, ABSOLUTE_MAX_QUESTIONS_PER_PAGE], 0 for non-content pages. Confidence: 0.8
