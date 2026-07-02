"""coderunner — a tiny, dependency-free code-execution sandbox for cgroup-v2 Kubernetes.

Replaces Judge0/isolate (which needs cgroup v1 + privileged + writable host cgroups — all things
k8s denies). Instead of a host-sandbox, we lean on the platform: this runs as a NON-ROOT pod with
no network (NetworkPolicy), a read-only root FS, and per-submission POSIX rlimits (CPU, address
space, processes, file size) plus a wall-clock timeout. Kubernetes enforces the cgroup-v2 memory
ceiling on the pod as the backstop.

Threat model: semi-trusted learners doing interview practice. This stops infinite loops, memory
bombs, fork bombs, runaway file writes, and network exfiltration. It is not VM-grade isolation
(no per-run namespaces) — for that you'd run this on a dedicated host with gVisor/isolate. Good,
honest sandboxing for the use case; documented as such.

Stdlib only (http.server); no external deps so the image stays minimal.
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# language_id (Judge0-compatible ids, so the app's LANGUAGES map is unchanged) -> spec.
# compile is a list (or None); run is a list. {src} is replaced with the source filename.
LANGUAGES: dict[int, dict] = {
    71: {"name": "Python (3)", "file": "main.py", "compile": None, "run": ["python3", "main.py"]},
    63: {"name": "JavaScript (Node.js)", "file": "main.js", "compile": None, "run": ["node", "main.js"]},
    50: {"name": "C (GCC)", "file": "main.c", "compile": ["gcc", "-O2", "-o", "main", "main.c", "-lm"], "run": ["./main"]},
    54: {"name": "C++ (G++)", "file": "main.cpp", "compile": ["g++", "-O2", "-std=c++17", "-o", "main", "main.cpp"], "run": ["./main"]},
}
DEFAULT_LANGUAGE_ID = 71

# Per-submission ceilings (env-overridable). AS (address space) is generous so the interpreter
# itself doesn't trip it; the pod's k8s memory limit is the hard backstop above this.
DEFAULT_CPU_SECONDS = int(os.getenv("RUN_CPU_SECONDS", "5"))
WALL_BUFFER_SECONDS = int(os.getenv("RUN_WALL_BUFFER", "5"))
MEM_LIMIT_BYTES = int(os.getenv("RUN_MEM_BYTES", str(512 * 1024 * 1024)))
NPROC_LIMIT = int(os.getenv("RUN_NPROC", "64"))
FSIZE_LIMIT = int(os.getenv("RUN_FSIZE_BYTES", str(16 * 1024 * 1024)))
COMPILE_TIMEOUT = 20
MAX_CONCURRENCY = int(os.getenv("RUN_MAX_CONCURRENCY", "4"))
MAX_OUTPUT = 64 * 1024  # truncate captured stdout/stderr

_slots = threading.Semaphore(MAX_CONCURRENCY)


def _preexec(cpu_seconds: int):
    """Runs in the child before exec: new session + hard resource limits."""
    def _set(what, limit):
        # Best-effort: some rlimits aren't settable on non-Linux dev hosts (e.g. macOS
        # RLIMIT_AS/NPROC). On the Linux runtime all of these apply.
        try:
            resource.setrlimit(what, limit)
        except (ValueError, OSError):
            pass

    def apply():
        # start_new_session=True already puts the child in its own session/group (so a
        # timeout can kill the whole tree); we just apply the resource limits here.
        _set(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        _set(resource.RLIMIT_AS, (MEM_LIMIT_BYTES, MEM_LIMIT_BYTES))
        _set(resource.RLIMIT_NPROC, (NPROC_LIMIT, NPROC_LIMIT))
        _set(resource.RLIMIT_FSIZE, (FSIZE_LIMIT, FSIZE_LIMIT))
        _set(resource.RLIMIT_CORE, (0, 0))
    return apply


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _run_once(cmd: list[str], cwd: str, stdin: str, cpu_seconds: int, wall: int) -> dict:
    """Run one command with limits; return {exit, stdout, stderr, timed_out, time}."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": cwd, "LANG": "C.UTF-8"}
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=_preexec(cpu_seconds), start_new_session=True, text=True,
        )
    except Exception as exc:
        return {"exit": -1, "stdout": "", "stderr": f"spawn failed: {exc}", "timed_out": False, "time": 0.0}
    try:
        out, err = proc.communicate(input=stdin, timeout=wall)
        return {
            "exit": proc.returncode,
            "stdout": (out or "")[:MAX_OUTPUT],
            "stderr": (err or "")[:MAX_OUTPUT],
            "timed_out": False,
            "time": round(time.monotonic() - start, 3),
        }
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            proc.communicate(timeout=2)
        except Exception:
            pass
        return {"exit": -1, "stdout": "", "stderr": "", "timed_out": True, "time": round(time.monotonic() - start, 3)}


def execute(source: str, language_id: int, stdin: str, cpu_seconds: int) -> dict:
    """Compile (if needed) + run; return a flat Judge0-ish result the app understands."""
    spec = LANGUAGES.get(language_id) or LANGUAGES[DEFAULT_LANGUAGE_ID]
    wall = cpu_seconds + WALL_BUFFER_SECONDS
    workdir = tempfile.mkdtemp(prefix="run_", dir="/tmp")
    try:
        with open(os.path.join(workdir, spec["file"]), "w") as f:
            f.write(source or "")

        if spec["compile"]:
            c = _run_once(spec["compile"], workdir, "", COMPILE_TIMEOUT, COMPILE_TIMEOUT)
            if c["timed_out"] or c["exit"] != 0:
                return {"status_id": 6, "status": "Compilation Error", "stdout": "",
                        "stderr": "", "compile_output": (c["stderr"] or "compile timed out")[:MAX_OUTPUT], "time": None}

        r = _run_once(spec["run"], workdir, stdin, cpu_seconds, wall)
        # Wall-clock kill OR the CPU rlimit firing (SIGXCPU) both mean "too slow".
        if r["timed_out"] or r["exit"] == -signal.SIGXCPU:
            return {"status_id": 5, "status": "Time Limit Exceeded", "stdout": r["stdout"],
                    "stderr": "", "compile_output": "", "time": r["time"]}
        if r["exit"] == 0:
            return {"status_id": 3, "status": "Accepted", "stdout": r["stdout"],
                    "stderr": r["stderr"], "compile_output": "", "time": r["time"]}
        return {"status_id": 11, "status": "Runtime Error (NZEC)", "stdout": r["stdout"],
                "stderr": r["stderr"] or f"exited with code {r['exit']}", "compile_output": "", "time": r["time"]}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/languages":
            return self._send(200, {"languages": [{"id": k, "label": v["name"]} for k, v in LANGUAGES.items()]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/run":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or "{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "invalid json"})
        source = str(body.get("source", ""))
        language_id = int(body.get("language_id", DEFAULT_LANGUAGE_ID) or DEFAULT_LANGUAGE_ID)
        stdin = str(body.get("stdin", ""))
        cpu_seconds = max(1, min(int(body.get("cpu_time_limit", DEFAULT_CPU_SECONDS) or DEFAULT_CPU_SECONDS), 15))
        if not _slots.acquire(timeout=30):
            return self._send(503, {"status_id": 13, "status": "Busy", "stdout": "", "stderr": "runner busy", "compile_output": "", "time": None})
        try:
            return self._send(200, execute(source, language_id, stdin, cpu_seconds))
        except Exception as exc:  # never 500 for a program failure
            return self._send(200, {"status_id": 13, "status": "Internal Error", "stdout": "", "stderr": str(exc)[:500], "compile_output": "", "time": None})
        finally:
            _slots.release()

    def log_message(self, *args):  # quiet
        pass


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        # Exercises the real sandbox path (needs python3 present).
        ok = execute("print(sum(map(int, input().split())))", 71, "2 3 4", 5)
        assert ok["status_id"] == 3 and ok["stdout"].strip() == "9", ok
        tle = execute("while True: pass", 71, "", 1)
        assert tle["status_id"] == 5, tle  # time limit
        err = execute("raise SystemExit(1)", 71, "", 5)
        assert err["status_id"] == 11, err  # non-zero exit
        print("coderunner selftest OK")
        sys.exit(0)

    port = int(os.getenv("PORT", "2358"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
