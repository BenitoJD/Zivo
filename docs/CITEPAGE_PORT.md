# Citepage → Zivo port tracker

**Mandate:** 100% citepage infra port except product exclusions (ChatMcqCard, zv-mcq in chat).

## Backend services (24/24)

- [x] `auth.py`
- [x] `chat_retrieval.py`
- [x] `chunking.py`
- [x] `chunks.py`
- [x] `demo_seed.py`
- [x] `dev_seed.py`
- [x] `document_create.py`
- [x] `embed.py`
- [x] `guest.py`
- [x] `guest_session.py`
- [x] `jobs.py`
- [x] `llm_registry.py`
- [x] `llm_router.py`
- [x] `parse.py`
- [x] `prompts.py`
- [x] `rerank.py`
- [x] `response_cache.py`
- [x] `retrieval.py`
- [x] `retrieval_gate.py`
- [x] `storage.py`
- [x] `usage.py`
- [x] `vision.py`
- [x] `web_import.py`

## ETA (16/16)

- [x] Full `backend/app/eta/` package + worker entrypoints

## Graphs

- [x] `chat_graph.py`
- [x] `mcq_graph.py`
- [x] `summarize_graph.py`
- [x] `generation_graph.py` (Zivo)
- [x] `evaluate_graph.py` (Zivo)
- [x] `remediation_graph.py` (Zivo)

## Excluded

- [ ] `ChatMcqCard` / `zv-mcq` in chat
- [x] Alembic baseline (`backend/alembic/versions/`; executes `schema/*.sql`)
- [ ] ORM copies of citepage `users` table (→ `qb.account`)

## Tests ported

- [x] `test_mcq.py`
- [x] `test_guest.py` (account_id rebrand)
- [x] Core unit tests passing locally (15 tests)
