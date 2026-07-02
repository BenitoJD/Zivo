"""Code execution via the self-hosted coderunner sandbox.

coderunner (``backend/coderunner/`` + ``infra/k8s/charts/coderunner``) compiles + runs
untrusted source and returns stdout/stderr/status. It's a Kubernetes-native sandbox (non-root
pod, read-only FS, no network via NetworkPolicy, per-submission POSIX rlimits) — chosen over
Judge0 because Judge0's isolate needs cgroup v1 + privileged, which our cgroup-v2 k3s denies.

The URL comes from settings (`code_runner_url`, or the `JUDGE0_URL` alias) — locally the
docker-compose service on :2358, in prod ``http://coderunner.coderunner.svc:2358``. This module
is the single reusable client; callers never talk to the sandbox directly.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import get_settings

# language ids (Judge0-compatible, so nothing else changes) → the label shown in the editor.
# Must stay in sync with coderunner's LANGUAGES. Add ids here + in the runner to support more.
LANGUAGES: dict[int, str] = {
    71: "Python (3)",
    63: "JavaScript (Node.js)",
    54: "C++ (G++)",
    50: "C (GCC)",
}
DEFAULT_LANGUAGE_ID = 71

# coderunner status ids: 3 = Accepted; 5 = TLE; 6 = Compile Error; 11 = Runtime Error; 13 = internal.
_ACCEPTED = 3

# Bound every call so a stuck sandbox can never hang a request.
_HTTP_TIMEOUT = 30.0


def _runner_url() -> str:
    s = get_settings()
    return (s.code_runner_url or s.judge0_url or "http://localhost:2358").rstrip("/")


async def run_code(
    source: str,
    language_id: int,
    stdin: str = "",
    *,
    cpu_time_limit: int = 5,
) -> dict[str, Any]:
    """Compile + run one submission, returning the flat result.

    Never raises for a *program* failure (compile error, runtime error, timeout) — those come
    back in the result dict. Raises RuntimeError only when the sandbox is unreachable.
    """
    if language_id not in LANGUAGES:
        language_id = DEFAULT_LANGUAGE_ID
    payload = {
        "source": source or "",
        "language_id": language_id,
        "stdin": stdin or "",
        "cpu_time_limit": cpu_time_limit,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(f"{_runner_url()}/run", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"code sandbox unavailable: {exc}") from exc
    return {
        "status_id": data.get("status_id"),
        "status": data.get("status") or "Unknown",
        "stdout": (data.get("stdout") or "").rstrip("\n"),
        "stderr": (data.get("stderr") or "").strip(),
        "compile_output": (data.get("compile_output") or "").strip(),
        "time": data.get("time"),
        "memory": data.get("memory"),
    }


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
    # ponytail: pure self-check — trim-compare + config, no live sandbox.
    assert _matches("5\n", "5")
    assert _matches("a \nb\n\n", "a\nb")  # trailing ws + blank lines ignored
    assert not _matches("5", "6")
    assert DEFAULT_LANGUAGE_ID in LANGUAGES
    print("code_execution self-check OK")
