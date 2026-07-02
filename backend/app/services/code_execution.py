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

# Judge0 language ids (from the CE language list) → the label we show in the editor.
# Kept small on purpose; add ids here as we support more languages.
LANGUAGES: dict[int, str] = {
    71: "Python (3.8)",
    63: "JavaScript (Node.js)",
    62: "Java (OpenJDK 13)",
    54: "C++ (GCC 9)",
    50: "C (GCC 9)",
    60: "Go (1.13)",
}
DEFAULT_LANGUAGE_ID = 71

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
    """Flatten a Judge0 submission result into the shape the app uses."""
    status = (result.get("status") or {})
    return {
        "status_id": status.get("id"),
        "status": status.get("description") or "Unknown",
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
    back in the normalized dict. Raises RuntimeError only when the sandbox is unreachable.
    """
    if language_id not in LANGUAGES:
        language_id = DEFAULT_LANGUAGE_ID
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
            if not token:
                raise RuntimeError("code sandbox returned no token")
            # Poll the privileged workers for the result.
            deadline = asyncio.get_event_loop().time() + _POLL_MAX
            while True:
                r = await client.get(
                    f"{base}/submissions/{token}",
                    params={"base64_encoded": "false"},
                )
                r.raise_for_status()
                result = r.json()
                if (result.get("status") or {}).get("id", 0) > _PROCESSING:
                    return _normalize(result)
                if asyncio.get_event_loop().time() > deadline:
                    raise RuntimeError("code sandbox timed out")
                await asyncio.sleep(_POLL_INTERVAL)
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
    if not tests:
        return {"passed": 0, "total": 0, "cases": [], "error": None}
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
        if ok:
            passed += 1
        cases.append({
            "ok": ok,
            "stdin": t.get("stdin", ""),
            "expected": expected,
            "stdout": r["stdout"],
            "stderr": r["stderr"] or r["compile_output"],
            "status": r["status"],
        })
    return {"passed": passed, "total": len(tests), "cases": cases, "error": None}


if __name__ == "__main__":  # pragma: no cover
    # ponytail: pure self-check — normalization + trim-compare, no live sandbox.
    n = _normalize({
        "status": {"id": 3, "description": "Accepted"},
        "stdout": "42\n", "stderr": None, "time": "0.01",
    })
    assert n["status_id"] == 3 and n["stdout"] == "42" and n["stderr"] == "", n

    assert _matches("5\n", "5")
    assert _matches("a \nb\n\n", "a\nb")  # trailing ws + blank lines ignored
    assert not _matches("5", "6")
    assert DEFAULT_LANGUAGE_ID in LANGUAGES
    print("code_execution self-check OK")
