from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import backend_env, database_url, python_bin, run_alembic, run_db_seed, validate_local_database_url
from .deps import start_deps
from .env import BACKEND_DIR, FRONTEND_DIR, ROOT, require_defaults, resolve_env
from .ports import allocate_app_ports, port_available

VENV = Path.home() / ".venv" / "citepage"
LOG_ROOT = ROOT / "logs" / "citepage-dev"


def worktree_hash() -> str:
    return hashlib.sha1(str(ROOT).encode()).hexdigest()[:10]


def normalize_instance(raw: str | None) -> str:
    import re

    value = (raw or f"wt-{worktree_hash()}").lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value).strip(".-")
    return value or "local"


@dataclass
class ProcSpec:
    key: str
    cwd: Path
    cmd: list[str]
    log: str
    env: dict[str, str]


def state_path(instance: str) -> Path:
    return LOG_ROOT / instance / "state.json"


def load_state(instance: str) -> dict:
    path = state_path(instance)
    if path.exists():
        return json.loads(path.read_text())
    return {"instance": instance, "processes": {}}


def save_state(instance: str, state: dict) -> None:
    path = state_path(instance)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def ensure_setup_done() -> None:
    if not (VENV / "bin" / "python").exists():
        raise RuntimeError("Missing ~/.venv/citepage. Run scripts/dev.sh setup first.")
    if not (FRONTEND_DIR / "node_modules").exists():
        raise RuntimeError("Missing frontend/node_modules. Run scripts/dev.sh setup first.")


def start_process(spec: ProcSpec, log_dir: Path) -> int:
    log_path = log_dir / spec.log
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            spec.cmd,
            cwd=spec.cwd,
            env=spec.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return proc.pid


def app_env(backend_port: int, frontend_port: int) -> tuple[dict[str, str], dict[str, str]]:
    resolved = resolve_env()
    backend = dict(resolved.backend)
    backend.update(
        {
            "DATABASE_URL": database_url(),
            "MINIO_ENDPOINT": backend.get("MINIO_ENDPOINT", "localhost:9010"),
            "PYTHONUNBUFFERED": "1",
        }
    )
    frontend = dict(os.environ)
    frontend.update(resolved.frontend)
    # Route API calls through the Vite proxy (same origin) instead of cross-origin fetch.
    frontend.pop("VITE_API_URL", None)
    frontend["CITEPAGE_BACKEND_URL"] = f"http://127.0.0.1:{backend_port}"
    return backend, frontend


def ensure_bucket(backend_env_dict: dict[str, str]) -> None:
    py = python_bin()
    code = "from app.services.storage import ensure_bucket; ensure_bucket()"
    proc = subprocess.run([py, "-c", code], cwd=BACKEND_DIR, env=backend_env_dict, text=True)
    if proc.returncode != 0:
        print("warning: could not ensure MinIO bucket (API may do this on startup)")


def start_app(
    instance_arg: str | None = None,
    backend_port_arg: int | None = None,
    frontend_port_arg: int | None = None,
) -> None:
    require_defaults()
    ensure_setup_done()
    instance = normalize_instance(instance_arg)
    current = load_state(instance)
    running = {n: i for n, i in current.get("processes", {}).items() if pid_running(i.get("pid"))}
    if running:
        raise RuntimeError(
            f"Instance {instance} already running: {', '.join(running)}. Use stop or restart."
        )

    backend_port, frontend_port = allocate_app_ports(backend_port_arg, frontend_port_arg)
    print("Ensuring shared dependencies are running...")
    start_deps()

    benv, fenv = app_env(backend_port, frontend_port)
    run_alembic("upgrade", "head", env=benv)
    print("Seeding local database (users, LLM registry)...")
    run_db_seed()
    ensure_bucket(benv)

    log_dir = LOG_ROOT / instance
    log_dir.mkdir(parents=True, exist_ok=True)
    py = python_bin()

    specs = [
        ProcSpec(
            "api",
            BACKEND_DIR,
            [py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(backend_port), "--reload"],
            "api.log",
            benv,
        ),
        ProcSpec(
            "worker_eta_io",
            BACKEND_DIR,
            [py, "run_eta_worker_async.py"],
            "worker-eta-io.log",
            {**benv, "ETA_WORKER_WORKLOADS": "io", "ETA_IO_CONCURRENCY": benv.get("ETA_IO_CONCURRENCY", "8")},
        ),
        ProcSpec(
            "worker_eta_cpu",
            BACKEND_DIR,
            [py, "run_eta_worker_cpu.py"],
            "worker-eta-cpu.log",
            {
                **benv,
                "ETA_WORKER_WORKLOADS": "cpu",
                "ETA_CPU_WORKER_MAX_CONCURRENCY": benv.get("ETA_CPU_WORKER_MAX_CONCURRENCY", "1"),
            },
        ),
        ProcSpec(
            "frontend",
            FRONTEND_DIR,
            ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port)],
            "frontend.log",
            fenv,
        ),
    ]

    processes = {}
    for spec in specs:
        processes[spec.key] = {
            "pid": start_process(spec, log_dir),
            "log": str((log_dir / spec.log).relative_to(ROOT)),
            "cmd": spec.cmd,
        }

    time.sleep(2)
    failed = [name for name, info in processes.items() if not pid_running(info["pid"])]
    if failed:
        stop_app(instance)
        raise RuntimeError(
            f"Failed to start: {', '.join(failed)}. Inspect: scripts/dev.sh logs --instance {instance}"
        )

    db = validate_local_database_url(benv["DATABASE_URL"])
    state = {
        "instance": instance,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "database_url": db.url,
        "processes": processes,
    }
    save_state(instance, state)
    print(f"Started citepage dev instance {instance}")
    print(f"API:      http://localhost:{backend_port}")
    print(f"Frontend: http://localhost:{frontend_port}")
    print(f"MinIO UI: http://localhost:9011")
    print(f"Database: {db.name} @ {db.host}")
    print(f"Logs:     {log_dir.relative_to(ROOT)}")


def stop_app(instance_arg: str | None = None) -> None:
    instance = normalize_instance(instance_arg)
    state = load_state(instance)
    for info in state.get("processes", {}).values():
        pid = info.get("pid")
        if pid_running(pid):
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
    time.sleep(1)
    for info in state.get("processes", {}).values():
        pid = info.get("pid")
        if pid_running(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
    state["stopped_at"] = datetime.now(timezone.utc).isoformat()
    save_state(instance, state)
    print(f"Stopped instance {instance}.")


def app_status(instance_arg: str | None = None) -> int:
    instance = normalize_instance(instance_arg)
    state = load_state(instance)
    print(
        json.dumps(
            {
                **state,
                "process_status": {
                    k: ("running" if pid_running(v.get("pid")) else "stopped")
                    for k, v in state.get("processes", {}).items()
                },
            },
            indent=2,
        )
    )
    return 0


def app_logs(instance_arg: str | None = None, lines: int = 200) -> None:
    instance = normalize_instance(instance_arg)
    log_dir = LOG_ROOT / instance
    if not log_dir.exists():
        print(f"No logs for instance {instance}")
        return
    for path in sorted(log_dir.glob("*.log")):
        print(f"===== {path.relative_to(ROOT)} =====")
        print("\n".join(path.read_text(errors="replace").splitlines()[-lines:]))
