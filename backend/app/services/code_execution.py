"""Code execution via a self-hosted Judge0 sandbox.

Judge0 (https://github.com/judge0/judge0) compiles + runs untrusted source in an isolated
sandbox and returns stdout/stderr/status. We use it for the interview coding rounds (run a
candidate's solution against hidden test cases) and, later, any coding-practice feature.

The sandbox URL comes from settings (`judge0_url`, env `JUDGE0_URL`). Judge0 runs on its own
dedicated box (it needs cgroup v1 + privileged isolate — see infra/judge0/README.md), reachable
over the network. This module is the single reusable client; callers never talk to Judge0 directly.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import get_settings
from app.engine_runtime import Pred, Rule, apply, first_match, pick

# Curated top-10 from Judge0 CE active catalogue (classic CE ids that typical
# self-hosted boxes still ship). Do not silently remap unknown ids — callers
# must 400 when ensure_language raises.
LANGUAGES: dict[int, str] = {
    71: "Python (3.8.1)",
    54: "C++ (GCC 9.2.0)",
    62: "Java (OpenJDK 13.0.1)",
    63: "JavaScript (Node.js 12.14.0)",
    50: "C (GCC 9.2.0)",
    60: "Go (1.13.5)",
    73: "Rust (1.40.0)",
    74: "TypeScript (3.7.4)",
    51: "C# (Mono 6.6.0.161)",
    78: "Kotlin (1.3.70)",
}
DEFAULT_LANGUAGE_ID = 71


def ensure_language(language_id: int) -> int:
    """Return language_id if allowlisted; raise ValueError otherwise."""
    lid = int(language_id)

    def _bad() -> int:
        raise ValueError(f"Unsupported language_id: {lid}")

    return pick(lid not in LANGUAGES, _bad, lambda: lid)

# Judge0 status ids: 1 = In Queue, 2 = Processing, 3 = Accepted; >3 are errors
# (Wrong Answer only applies when we submit expected_output, which we don't — we compare
# ourselves so we can trim + show diffs).
_ACCEPTED = 3
_PROCESSING = 2  # status_id <= this means the run isn't finished yet

# We submit async (wait=false) and poll: wait=true makes Judge0 run the job *inline in the
# web process*, which isn't the privileged worker, so the isolate sandbox can't start. Async
# routes the job to the privileged workers (the only ones that can run isolate). See
# infra/judge0/README.md. These bound total client time so a stuck sandbox can't hang a request.
_HTTP_TIMEOUT = 20.0
_POLL_INTERVAL = 0.4
_POLL_MAX = 30.0


def _normalize(result: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Judge0 submission result into the shape the app uses.

    Free runs (no expected_output) never get Judge0 "Wrong Answer" — status 3
    means the process finished cleanly. Surface that as "Ran" so the UI does not
    imply the sample answer was correct (Submit owns pass/fail).
    """
    status = (result.get("status") or {})
    status_id = status.get("id")
    description = status.get("description") or "Unknown"
    description = pick(
        status_id == _ACCEPTED or str(description).strip().lower() == "accepted",
        lambda: "Ran",
        lambda: description,
    )
    return {
        "status_id": status_id,
        "status": description,
        "stdout": (result.get("stdout") or "").rstrip("\n"),
        "stderr": (result.get("stderr") or "").strip(),
        "compile_output": (result.get("compile_output") or "").strip(),
        "time": result.get("time"),
        "memory": result.get("memory"),
    }


async def run_code(
    source: str,
    language_id: int,
    stdin: str = "",
    *,
    cpu_time_limit: float = 5.0,
    memory_limit_kb: int = 128000,
) -> dict[str, Any]:
    """Compile + run one submission, returning the normalized result.

    Never raises for a *program* failure (compile error, runtime error, timeout) — those come
    back in the normalized dict. Raises ValueError for an unsupported language_id.
    Raises RuntimeError only when the sandbox is unreachable.
    """
    language_id = ensure_language(language_id)
    base = get_settings().judge0_url.rstrip("/")
    payload = {
        "source_code": source or "",
        "language_id": language_id,
        "stdin": stdin or "",
        "cpu_time_limit": cpu_time_limit,
        "memory_limit": memory_limit_kb,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{base}/submissions",
                params={"base64_encoded": "false", "wait": "false"},
                json=payload,
            )
            resp.raise_for_status()
            token = resp.json().get("token")

            def _no_token() -> None:
                raise RuntimeError("code sandbox returned no token")

            pick(not token, _no_token, lambda: None)
            deadline = asyncio.get_event_loop().time() + _POLL_MAX
            done: list[dict[str, Any]] = []
            while not done:
                r = await client.get(
                    f"{base}/submissions/{token}",
                    params={"base64_encoded": "false"},
                )
                r.raise_for_status()
                result = r.json()
                finished = (result.get("status") or {}).get("id", 0) > _PROCESSING
                timed_out = asyncio.get_event_loop().time() > deadline
                hit = first_match(
                    (
                        Rule(when=(Pred("finished", "truthy"),), action="done"),
                        Rule(when=(Pred("timed_out", "truthy"),), action="timeout"),
                        Rule(when=(), action="wait"),
                    ),
                    {"finished": finished, "timed_out": timed_out},
                )

                def _timeout() -> None:
                    raise RuntimeError("code sandbox timed out")

                apply(
                    hit.action,
                    {
                        "done": lambda: done.append(_normalize(result)),
                        "timeout": _timeout,
                        "wait": lambda: None,
                    },
                )
                await pick(not done, lambda: asyncio.sleep(_POLL_INTERVAL), lambda: asyncio.sleep(0))
            return done[0]
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"code sandbox unavailable: {exc}") from exc


def _matches(actual: str, expected: str) -> bool:
    """Trim-compare: ignore trailing whitespace per line + trailing blank lines."""
    def norm(s: str) -> str:
        return "\n".join(line.rstrip() for line in (s or "").split("\n")).rstrip("\n")

    return norm(actual) == norm(expected)


async def run_tests(
    source: str, language_id: int, tests: list[dict[str, str]]
) -> dict[str, Any]:
    """Run the source against each {stdin, expected_output} case; report pass/fail.

    Cases run concurrently (each is an independent sandbox submission). If the sandbox is
    unreachable the whole batch fails closed with an error field.
    """

    async def _empty() -> dict[str, Any]:
        return {"passed": 0, "total": 0, "cases": [], "error": None}

    return await pick(not tests, _empty, lambda: _run_tests_body(source, language_id, tests))


async def _run_tests_body(source: str, language_id: int, tests: list[dict[str, str]]) -> dict[str, Any]:
    try:
        results = await asyncio.gather(
            *(run_code(source, language_id, t.get("stdin", "")) for t in tests)
        )
    except RuntimeError as exc:
        return {"passed": 0, "total": len(tests), "cases": [], "error": str(exc)}

    cases: list[dict[str, Any]] = []
    passed = 0
    for t, r in zip(tests, results):
        expected = t.get("expected_output", "")
        ok = r["status_id"] == _ACCEPTED and _matches(r["stdout"], expected)
        passed += int(ok)
        cases.append({
            "ok": ok,
            "stdin": t.get("stdin", ""),
            "expected": expected,
            "stdout": r["stdout"],
            "stderr": r["stderr"] or r["compile_output"],
            "status": r["status"],
        })
    return {"passed": passed, "total": len(tests), "cases": cases, "error": None}


def _self_check() -> None:
    n = _normalize({
        "status": {"id": 3, "description": "Accepted"},
        "stdout": "42\n", "stderr": None, "time": "0.01",
    })
    assert n["status_id"] == 3 and n["stdout"] == "42" and n["stderr"] == "", n
    assert _matches("5\n", "5")
    assert _matches("a \nb\n\n", "a\nb")
    assert not _matches("5", "6")
    assert DEFAULT_LANGUAGE_ID in LANGUAGES
    print("code_execution self-check OK")


pick(__name__ == "__main__", _self_check, lambda: None)
