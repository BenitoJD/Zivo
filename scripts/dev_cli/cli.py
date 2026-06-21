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
from .env import BACKEND_DEFAULTS, BACKEND_DIR, ROOT, require_defaults
from .ports import allocate_backend_port

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
    run([str(VENV / "bin" / "pip"), "install", "-q", "-r", str(BACKEND_DIR / "requirements.txt")])
    local_env = BACKEND_DIR / ".env.local"
    if not local_env.exists():
        print(f"Tip: copy overrides to {local_env.relative_to(ROOT)}")
    print("Setup complete.")
    return 0


def doctor(_: argparse.Namespace) -> int:
    status = 0
    print("zivo dev doctor")
    print(f"{'✓' if BACKEND_DEFAULTS.exists() else '✗'} {BACKEND_DEFAULTS.relative_to(ROOT)}")
    print(f"{'✓' if (VENV / 'bin' / 'python').exists() else '✗'} venv: {VENV}")
    if deps_status() != 0:
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


def start(args: argparse.Namespace) -> int:
    require_defaults()
    if not (VENV / "bin" / "python").exists():
        raise RuntimeError("Run ./scripts/dev.sh setup first.")

    state = load_state()
    if pid_running(state.get("api_pid")):
        raise RuntimeError("API already running. Use ./scripts/dev.sh stop first.")

    port = allocate_backend_port(args.port)
    start_deps()
    benv = backend_env()
    apply_schema(benv)

    log_path = LOG_ROOT / "api.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            [python_bin(), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--reload"],
            cwd=BACKEND_DIR,
            env=benv,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    save_state({"api_pid": proc.pid, "api_port": port})
    print(f"API running at http://127.0.0.1:{port} (logs: {log_path.relative_to(ROOT)})")
    return 0


def stop(_: argparse.Namespace) -> int:
    state = load_state()
    pid = state.get("api_pid")
    if pid_running(pid):
        os.kill(pid, signal.SIGTERM)
        print(f"Stopped API (pid {pid})")
    save_state({})
    return 0


def db_cmd(args: argparse.Namespace) -> int:
    if args.db_command == "schema":
        start_deps()
        apply_schema(backend_env())
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
    db_p.add_argument("db_command", choices=["schema"])
    db_p.set_defaults(func=db_cmd)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
