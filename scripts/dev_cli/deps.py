from __future__ import annotations

import platform
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from .env import ROOT, resolve_env

COMPOSE_FILE = ROOT / "docker-compose.yml"
ZIVO_PG_PORT = 5455
ZIVO_MINIO_API_PORT = 9020


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, **kwargs)


def require_tool(tool: str) -> None:
    if shutil.which(tool) is None:
        raise RuntimeError(f"{tool} is required but was not found on PATH.")


def docker_available() -> bool:
    return run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def compose_service_id(service: str) -> str | None:
    proc = run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "ps", "-q", service],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    cid = proc.stdout.strip()
    if not cid:
        return None
    inspect = run(
        ["docker", "inspect", "-f", "{{.State.Running}}", cid],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return cid if inspect.stdout.strip() == "true" else None


def wait_for_postgres(container_id: str, timeout: int = 45) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if (
            run(
                ["docker", "exec", container_id, "pg_isready", "-U", "zivo", "-d", "zivo"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        ):
            return
        time.sleep(1)
    raise RuntimeError("Postgres did not become ready in time.")


def http_ok(url: str) -> bool:
    return run(
        ["curl", "-fsS", "--max-time", "2", url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def wait_for_minio(timeout: int = 45) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{ZIVO_MINIO_API_PORT}/minio/health/live"
    while time.time() < deadline:
        if http_ok(url):
            return
        time.sleep(1)
    raise RuntimeError(f"MinIO did not become ready on port {ZIVO_MINIO_API_PORT}.")


def port_available_local(port: int) -> bool:
    from .ports import port_available

    return port_available(port)


def start_deps() -> None:
    require_tool("docker")
    if not docker_available():
        hint = "Start Docker Desktop" if Path("/Applications/Docker.app").exists() else "Start docker"
        if platform.system() == "Linux":
            hint = "sudo systemctl start docker"
        raise RuntimeError(f"Docker daemon is not reachable. {hint}")

    services: list[str] = []
    if not compose_service_id("postgres"):
        if not port_available_local(ZIVO_PG_PORT):
            raise RuntimeError(f"Port {ZIVO_PG_PORT} is occupied.")
        services.append("postgres")
    if not compose_service_id("minio"):
        if not port_available_local(ZIVO_MINIO_API_PORT):
            if not http_ok(f"http://127.0.0.1:{ZIVO_MINIO_API_PORT}/minio/health/live"):
                raise RuntimeError(f"Port {ZIVO_MINIO_API_PORT} is occupied.")
        else:
            services.append("minio")

    if services:
        print(f"Starting Docker dependencies: {' '.join(services)}")
        proc = run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", *services])
        if proc.returncode != 0:
            raise RuntimeError("docker compose failed to start dependencies.")

    pg_cid = compose_service_id("postgres")
    if pg_cid:
        wait_for_postgres(pg_cid)
    wait_for_minio()
    print("Dependencies ready (postgres, minio).")


def deps_status() -> int:
    status = 0
    print("Dependency status:")
    if shutil.which("docker") and docker_available():
        pg = compose_service_id("postgres")
        minio = compose_service_id("minio")
        print(f"- Postgres: {'running' if pg else 'not running'}")
        print(f"- MinIO: {'running' if minio else 'not running'}")
        if not pg or not minio:
            status = 1
    else:
        print("- Docker: unavailable")
        status = 1
    return status
