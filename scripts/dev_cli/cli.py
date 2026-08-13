from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from .db import apply_schema, backend_env, python_bin
from .deps import deps_status, start_deps
from .env import (
    AUTH_DIR,
    BACKEND_DEFAULTS,
    BACKEND_DIR,
    CONTENT_DIR,
    FRONTEND_DIR,
    PRACTICE_DIR,
    ROOT,
    STORAGE_DIR,
    STUDY_DIR,
    require_defaults,
)
from .ports import (
    allocate_auth_port,
    allocate_backend_port,
    allocate_content_port,
    allocate_practice_port,
    allocate_storage_port,
    allocate_study_port,
)

FRONTEND_PORT = 3000

VENV = Path.home() / ".venv" / "zivo"
LOG_ROOT = ROOT / "logs" / "zivo-dev"
STATE_FILE = LOG_ROOT / "state.json"


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, text=True, check=check)


def find_python() -> str:
    for name in ("python3.12", "python3"):
        path = shutil.which(name)
        if path:
            proc = subprocess.run(
                [path, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode == 0:
                return path
    raise RuntimeError("python >= 3.12 is required.")


def setup(_: argparse.Namespace) -> int:
    require_defaults()
    if not VENV.exists():
        VENV.parent.mkdir(parents=True, exist_ok=True)
        run([find_python(), "-m", "venv", str(VENV)])
    # Dev venv gets pytest/ruff too (requirements-dev pulls in requirements.txt).
    run([str(VENV / "bin" / "pip"), "install", "-q", "-r", str(BACKEND_DIR / "requirements-dev.txt")])
    run([str(VENV / "bin" / "pip"), "install", "-q", "-r", str(AUTH_DIR / "requirements-dev.txt")])
    run([str(VENV / "bin" / "pip"), "install", "-q", "-r", str(STORAGE_DIR / "requirements-dev.txt")])
    local_env = BACKEND_DIR / ".env.local"
    if not local_env.exists():
        print(f"Tip: copy overrides to {local_env.relative_to(ROOT)}")
    print("Setup complete.")
    return 0


def doctor(_: argparse.Namespace) -> int:
    status = 0
    print("zivo dev doctor")
    print(f"{'✓' if BACKEND_DEFAULTS.exists() else '✗'} {BACKEND_DEFAULTS.relative_to(ROOT)}")
    print(f"{'✓' if (AUTH_DIR / '.env.example').exists() else '✗'} {(AUTH_DIR / '.env.example').relative_to(ROOT)}")
    print(f"{'✓' if (STORAGE_DIR / '.env.example').exists() else '✗'} {(STORAGE_DIR / '.env.example').relative_to(ROOT)}")
    print(f"{'✓' if (VENV / 'bin' / 'python').exists() else '✗'} venv: {VENV}")
    if deps_status() != 0:
        status = 1
    judge0_url = backend_env().get("JUDGE0_URL", "http://localhost:2358")
    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(f"{judge0_url.rstrip('/')}/about", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ok = 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        ok = False
    label = "Judge0 sandbox" if ok else "Judge0 sandbox (unreachable — set JUDGE0_URL in backend/.env.local)"
    print(f"{'✓' if ok else '✗'} {label}: {judge0_url}")
    if not ok:
        status = 1
    return status


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _require_npm() -> str:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is required for the frontend. Install Node.js 22+.")
    return npm


def _ensure_frontend_deps(npm: str) -> None:
    if not FRONTEND_DIR.is_dir():
        raise RuntimeError(f"Missing frontend directory: {FRONTEND_DIR.relative_to(ROOT)}")
    if not (FRONTEND_DIR / "node_modules").exists():
        print("Installing frontend dependencies…")
        run([npm, "install"], cwd=FRONTEND_DIR)


def start(args: argparse.Namespace) -> int:
    require_defaults()
    if not (VENV / "bin" / "python").exists():
        raise RuntimeError("Run ./scripts/dev.sh setup first.")

    state = load_state()
    if pid_running(state.get("api_pid")) or pid_running(state.get("auth_pid")) or pid_running(state.get("storage_pid")) or pid_running(state.get("practice_pid")) or pid_running(state.get("content_pid")) or pid_running(state.get("study_pid")) or pid_running(state.get("frontend_pid")):
        raise RuntimeError("Dev stack already running. Use ./scripts/dev.sh stop first.")

    port = allocate_backend_port(args.port)
    auth_port = allocate_auth_port()
    storage_port = allocate_storage_port()
    practice_port = allocate_practice_port()
    content_port = allocate_content_port()
    study_port = allocate_study_port()
    start_deps()
    benv = backend_env()
    benv["STORAGE_URL"] = f"http://127.0.0.1:{storage_port}"
    from .db import (
        run_alembic,
        run_auth_alembic,
        run_content_alembic,
        run_practice_alembic,
        run_storage_alembic,
        run_study_alembic,
    )

    run_auth_alembic("upgrade", "head", env=benv)
    run_storage_alembic("upgrade", "head", env=benv)
    run_practice_alembic("upgrade", "head", env=benv)
    run_content_alembic("upgrade", "head", env=benv)
    run_study_alembic("upgrade", "head", env=benv)
    run_alembic("upgrade", "head", env=benv)
    subprocess.run(
        [python_bin(), "scripts/seed_question_vocab.py"],
        cwd=BACKEND_DIR,
        env=benv,
        check=False,
    )
    subprocess.run(
        [python_bin(), "scripts/seed_dev.py"],
        cwd=BACKEND_DIR,
        env=benv,
        check=False,
    )
    subprocess.run(
        [python_bin(), "scripts/seed_system_design_bank.py"],
        cwd=BACKEND_DIR,
        env=benv,
        check=False,
    )

    log_path = LOG_ROOT / "api.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    py = python_bin()
    with log_path.open("ab") as log:
        api_proc = subprocess.Popen(
            [py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--reload"],
            cwd=BACKEND_DIR,
            env=benv,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    auth_log = LOG_ROOT / "auth.log"
    with auth_log.open("ab") as auth_out:
        auth_proc = subprocess.Popen(
            [py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(auth_port), "--reload"],
            cwd=AUTH_DIR,
            env=benv,
            stdout=auth_out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    storage_log = LOG_ROOT / "storage.log"
    with storage_log.open("ab") as storage_out:
        storage_proc = subprocess.Popen(
            [py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(storage_port), "--reload"],
            cwd=STORAGE_DIR,
            env=benv,
            stdout=storage_out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    product_env = {**benv, "PYTHONPATH": str(BACKEND_DIR)}
    practice_log = LOG_ROOT / "practice.log"
    with practice_log.open("ab") as practice_out:
        practice_proc = subprocess.Popen(
            [
                py, "-m", "uvicorn", "practice_main:app",
                "--host", "127.0.0.1", "--port", str(practice_port),
                "--reload", "--reload-dir", str(PRACTICE_DIR), "--reload-dir", str(BACKEND_DIR),
            ],
            cwd=PRACTICE_DIR,
            env=product_env,
            stdout=practice_out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    content_log = LOG_ROOT / "content.log"
    with content_log.open("ab") as content_out:
        content_proc = subprocess.Popen(
            [
                py, "-m", "uvicorn", "content_main:app",
                "--host", "127.0.0.1", "--port", str(content_port),
                "--reload", "--reload-dir", str(CONTENT_DIR), "--reload-dir", str(BACKEND_DIR),
            ],
            cwd=CONTENT_DIR,
            env=product_env,
            stdout=content_out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    study_log = LOG_ROOT / "study.log"
    with study_log.open("ab") as study_out:
        study_proc = subprocess.Popen(
            [
                py, "-m", "uvicorn", "study_main:app",
                "--host", "127.0.0.1", "--port", str(study_port),
                "--reload", "--reload-dir", str(STUDY_DIR), "--reload-dir", str(BACKEND_DIR),
            ],
            cwd=STUDY_DIR,
            env=product_env,
            stdout=study_out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    io_log = LOG_ROOT / "worker-io.log"
    cpu_log = LOG_ROOT / "worker-cpu.log"
    with io_log.open("ab") as io_out:
        io_proc = subprocess.Popen(
            [py, "run_eta_worker_async.py"],
            cwd=BACKEND_DIR,
            env={
                **benv,
                "ETA_WORKER_WORKLOADS": "io",
                "ETA_IO_CONCURRENCY": os.getenv("ETA_IO_CONCURRENCY", "16"),
            },
            stdout=io_out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    with cpu_log.open("ab") as cpu_out:
        cpu_proc = subprocess.Popen(
            [py, "run_eta_worker_cpu.py"],
            cwd=BACKEND_DIR,
            env={
                **benv,
                "ETA_WORKER_WORKLOADS": "cpu",
                "ETA_CPU_WORKER_MAX_CONCURRENCY": os.getenv("ETA_CPU_WORKER_MAX_CONCURRENCY", "4"),
            },
            stdout=cpu_out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    npm = _require_npm()
    _ensure_frontend_deps(npm)
    frontend_log = LOG_ROOT / "frontend.log"
    frontend_env = {
        **os.environ,
        "API_PROXY_URL": f"http://127.0.0.1:{port}",
        "AUTH_PROXY_URL": f"http://127.0.0.1:{auth_port}",
        "STORAGE_PROXY_URL": f"http://127.0.0.1:{storage_port}",
        "PRACTICE_PROXY_URL": f"http://127.0.0.1:{practice_port}",
        "CONTENT_PROXY_URL": f"http://127.0.0.1:{content_port}",
        "STUDY_PROXY_URL": f"http://127.0.0.1:{study_port}",
    }
    with frontend_log.open("ab") as fe_out:
        frontend_proc = subprocess.Popen(
            [npm, "run", "dev"],
            cwd=FRONTEND_DIR,
            env=frontend_env,
            stdout=fe_out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    save_state(
        {
            "api_pid": api_proc.pid,
            "api_port": port,
            "auth_pid": auth_proc.pid,
            "auth_port": auth_port,
            "storage_pid": storage_proc.pid,
            "storage_port": storage_port,
            "practice_pid": practice_proc.pid,
            "practice_port": practice_port,
            "content_pid": content_proc.pid,
            "content_port": content_port,
            "study_pid": study_proc.pid,
            "study_port": study_port,
            "io_pid": io_proc.pid,
            "cpu_pid": cpu_proc.pid,
            "frontend_pid": frontend_proc.pid,
            "frontend_port": FRONTEND_PORT,
        }
    )
    print(f"API running at http://127.0.0.1:{port} (logs: {log_path.relative_to(ROOT)})")
    print(f"Auth running at http://127.0.0.1:{auth_port} (logs: {auth_log.relative_to(ROOT)})")
    print(f"Storage running at http://127.0.0.1:{storage_port} (logs: {storage_log.relative_to(ROOT)})")
    print(f"Practice running at http://127.0.0.1:{practice_port} (logs: {practice_log.relative_to(ROOT)})")
    print(f"Content running at http://127.0.0.1:{content_port} (logs: {content_log.relative_to(ROOT)})")
    print(f"Study running at http://127.0.0.1:{study_port} (logs: {study_log.relative_to(ROOT)})")
    print(f"Workers: IO pid {io_proc.pid}, CPU pid {cpu_proc.pid}")
    print(
        f"Frontend at http://localhost:{FRONTEND_PORT} "
        f"(logs: {frontend_log.relative_to(ROOT)}, proxy → :{port} auth → :{auth_port} "
        f"storage → :{storage_port} practice → :{practice_port} content → :{content_port} study → :{study_port})"
    )
    return 0


def stop(_: argparse.Namespace) -> int:
    state = load_state()
    for key in ("frontend_pid", "api_pid", "auth_pid", "storage_pid", "practice_pid", "content_pid", "study_pid", "io_pid", "cpu_pid"):
        pid = state.get(key)
        if pid_running(pid):
            os.kill(pid, signal.SIGTERM)
            print(f"Stopped {key} (pid {pid})")
    save_state({})
    return 0


def db_cmd(args: argparse.Namespace) -> int:
    if args.db_command == "migrate":
        start_deps()
        from .db import (
            run_alembic,
            run_auth_alembic,
            run_content_alembic,
            run_practice_alembic,
            run_storage_alembic,
            run_study_alembic,
        )

        run_auth_alembic("upgrade", "head")
        run_storage_alembic("upgrade", "head")
        run_practice_alembic("upgrade", "head")
        run_content_alembic("upgrade", "head")
        run_study_alembic("upgrade", "head")
        run_alembic("upgrade", "head")
    elif args.db_command == "schema":
        start_deps()
        apply_schema(backend_env())
    elif args.db_command == "qb-schema":
        start_deps()
        from .db import apply_qb_schema

        apply_qb_schema(backend_env())
    elif args.db_command == "seed":
        start_deps()
        subprocess.run(
            [python_bin(), "scripts/seed_question_vocab.py"],
            cwd=BACKEND_DIR,
            env=backend_env(),
            check=True,
        )
        subprocess.run(
            [python_bin(), "scripts/seed_dev.py"],
            cwd=BACKEND_DIR,
            env=backend_env(),
            check=True,
        )
        subprocess.run(
            [python_bin(), "scripts/seed_system_design_bank.py"],
            cwd=BACKEND_DIR,
            env=backend_env(),
            check=True,
        )
    else:
        raise RuntimeError(f"unknown db command: {args.db_command}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev.sh")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup").set_defaults(func=setup)
    sub.add_parser("doctor").set_defaults(func=doctor)
    sub.add_parser("stop").set_defaults(func=stop)

    start_p = sub.add_parser("start")
    start_p.add_argument("--port", type=int, default=None)
    start_p.set_defaults(func=start)

    db_p = sub.add_parser("db")
    db_p.add_argument("db_command", choices=["migrate", "schema", "qb-schema", "seed"])
    db_p.set_defaults(func=db_cmd)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
