# Offline Mode ships answer keys to the device

**Date:** 2026-08-01 · **Status:** accepted

Offline Mode lets a learner download a signed, expiring "pack" for an artifact —
the full active question pool **with the answer key + pre-baked per-option
feedback** plus their mastery snapshot — study it with no network, and replay
grades through the real engines on reconnect. This requires the correct answer
to reach the device: there is no server to ask mid-grade offline. Until now,
`assertions.py:_sanitize_assertion_payload` stripped `correct_index` /
`correct_indices` / `explanation` / `option_feedback` from every payload before
it left the server. Offline Mode is the one deliberate exception.

**Why.** The product's users study while travelling, where signal is flaky or
absent for long stretches. Server-authoritative grading cannot run with no
server. The pack is the unit that makes offline study possible: pre-load while
online, study fully offline, sync when back. This was a requested feature, not a
speculative build.

**Scope of the break, and the mitigation.**

- Packs ship keys only. Every *other* read path still sanitizes. The pack
  builder (`app/services/offline_pack.build_pack`) reads raw `intel.assertion`
  rows directly and does **not** modify `_sanitize_assertion_payload`.
- Packs are signed (HMAC over `Settings.secret_key`) and short-lived
  (`Settings.offline_pack_ttl_days`, default 7). A tampered pack fails
  `verify_pack` and a grade-batch replay with a forged signature is rejected.
  The client refuses a pack whose `schema_version` it does not understand.
- Personal study only. Packs are never served for competitive / exam modes.
  Keys ride along so a learner can self-assess offline; a commuter extracting
  answers from their own browser gains nothing.
- The one-way door is acknowledged: once keys ship to clients, this cannot be
  quietly un-shipped. It is gated behind `Settings.offline_mode_enabled`
  (default off) so production behaviour is unchanged until explicitly enabled.

**Considered and rejected.**

- *Offline review only (no new grading).* Keeps the trust model fully intact
  but delivers far less value: a learner could re-read graded questions, not
  study new ones. Rejected because the requested feature is offline *study*.
- *Verdict-only offline (no keys).* The device would have to defer the verdict
  until sync, so the learner answers into a void with no immediate right/wrong.
  Rejected as a poor experience that defeats the point of studying offline.

**Consequence.** Server authority is preserved at the system level: grading
correctness is **recomputed** from the stored key on sync (`grade_verdict` over
the raw payload), never taken from a client `correct` flag; the real adaptive,
calibration, and mastery engines run on replay; the reconciled queue state
overwrites the client's local projection. Offline is a deferred submission, not
a parallel source of truth. The engines are not forked to the client; only the
pure index-compare (`grade_verdict`) is mirrored locally for instant verdicts.
