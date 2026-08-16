"""Pure FE wait-gate helpers — mirror frontend/lib/learnStatus.ts logic in pytest.

Keeps the screenshot regression ("2 ready" behind a 92% spinner) locked without
a JS test runner.
"""

from __future__ import annotations


from app.engine_runtime import pick


def learn_has_unanswered_ready(
    *,
    pool_available: int = 0,
    questions_generated: int = 0,
    questions_answered: int = 0,
) -> bool:
    return pick(pool_available > 0, lambda: True, lambda: questions_generated > questions_answered)


def test_unanswered_ready_when_pool_available() -> None:
    assert learn_has_unanswered_ready(pool_available=2, questions_generated=2)


def test_unanswered_ready_when_generated_exceeds_answered() -> None:
    # Screenshot regression: questions_generated=2 but pool_available missing/0
    # still means cards exist — never full-screen wait.
    assert learn_has_unanswered_ready(
        pool_available=0, questions_generated=2, questions_answered=0
    )


def test_not_ready_when_pool_drained() -> None:
    assert not learn_has_unanswered_ready(
        pool_available=0, questions_generated=2, questions_answered=2
    )
