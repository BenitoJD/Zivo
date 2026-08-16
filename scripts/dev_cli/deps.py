from __future__ import annotations

import platform
import shutil
import subprocess
import time
from pathlib import Path

from .env import ROOT, Pred, Rule, _raise, apply, choose, first_match, pick

COMPOSE_FILE = ROOT / "docker-compose.yml"
ZIVO_PG_PORT = 5455
ZIVO_MINIO_API_PORT = 9020


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, **kwargs)


def require_tool(tool: str) -> None:
    pick(
        shutil.which(tool) is None,
        lambda: _raise(RuntimeError(f"{tool} is required but was not found on PATH.")),
        lambda: None,
    )


def docker_available() -> bool:
    return run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def compose_service_id(service: str) -> str | None:
    proc = run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "ps", "-q", service],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    cid = proc.stdout.strip()
    def _inspect() -> str | None:
        inspect = run(
            ["docker", "inspect", "-f", "{{.State.Running}}", cid],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return pick(inspect.stdout.strip() == "true", lambda: cid, lambda: None)

    return pick(not cid, lambda: None, _inspect)


def wait_for_postgres(container_id: str, timeout: int = 45) -> None:
    deadline = time.time() + timeout

    def go() -> None:
        remaining = time.time() < deadline

        def _try() -> None:
            ready = (
                run(
                    ["docker", "exec", container_id, "pg_isready", "-U", "zivo", "-d", "zivo"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            )
            pick(ready, lambda: None, lambda: (time.sleep(1), go())[1])

        pick(remaining, _try, lambda: _raise(RuntimeError("Postgres did not become ready in time.")))

    go()


def http_ok(url: str) -> bool:
    return run(
        ["curl", "-fsS", "--max-time", "2", url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def wait_for_minio(timeout: int = 45) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{ZIVO_MINIO_API_PORT}/minio/health/live"

    def go() -> None:
        remaining = time.time() < deadline

        def _try() -> None:
            pick(http_ok(url), lambda: None, lambda: (time.sleep(1), go())[1])

        pick(
            remaining,
            _try,
            lambda: _raise(RuntimeError(f"MinIO did not become ready on port {ZIVO_MINIO_API_PORT}.")),
        )

    go()


def port_available_local(port: int) -> bool:
    from .ports import port_available

    return port_available(port)


def start_deps() -> None:
    require_tool("docker")
    pick(
        not docker_available(),
        lambda: _raise(
            RuntimeError(
                "Docker daemon is not reachable. "
                + apply(
                    first_match(
                        (
                            Rule(when=(Pred("linux", "truthy"),), action="linux"),
                            Rule(when=(Pred("desktop", "truthy"),), action="desktop"),
                            Rule(when=(), action="generic"),
                        ),
                        {
                            "linux": platform.system() == "Linux",
                            "desktop": Path("/Applications/Docker.app").exists(),
                        },
                    ).action,
                    {
                        "linux": lambda: "sudo systemctl start docker",
                        "desktop": lambda: "Start Docker Desktop",
                        "generic": lambda: "Start docker",
                    },
                )
            )
        ),
        lambda: None,
    )

    services: list[str] = []

    def _need_postgres() -> None:
        pick(
            not port_available_local(ZIVO_PG_PORT),
            lambda: _raise(RuntimeError(f"Port {ZIVO_PG_PORT} is occupied.")),
            lambda: services.append("postgres"),
        )

    def _need_minio() -> None:
        def _occupied() -> None:
            pick(
                not http_ok(f"http://127.0.0.1:{ZIVO_MINIO_API_PORT}/minio/health/live"),
                lambda: _raise(RuntimeError(f"Port {ZIVO_MINIO_API_PORT} is occupied.")),
                lambda: None,
            )

        pick(not port_available_local(ZIVO_MINIO_API_PORT), _occupied, lambda: services.append("minio"))

    pick(not compose_service_id("postgres"), _need_postgres, lambda: None)
    pick(not compose_service_id("minio"), _need_minio, lambda: None)

    def _up() -> None:
        print(f"Starting Docker dependencies: {' '.join(services)}")
        proc = run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", *services])
        pick(
            proc.returncode != 0,
            lambda: _raise(RuntimeError("docker compose failed to start dependencies.")),
            lambda: None,
        )

    pick(bool(services), _up, lambda: None)
    pg_cid = compose_service_id("postgres")
    pick(bool(pg_cid), lambda: wait_for_postgres(pg_cid), lambda: None)
    wait_for_minio()
    print("Dependencies ready (postgres, minio).")


def deps_status() -> int:
    print("Dependency status:")

    def _ok() -> int:
        pg = compose_service_id("postgres")
        minio = compose_service_id("minio")
        print(f"- Postgres: {choose(bool(pg), 'running', 'not running')}")
        print(f"- MinIO: {choose(bool(minio), 'running', 'not running')}")
        return choose(not pg or not minio, 1, 0)

    def _missing() -> int:
        print("- Docker: unavailable")
        return 1

    return pick(bool(shutil.which("docker") and docker_available()), _ok, _missing)
