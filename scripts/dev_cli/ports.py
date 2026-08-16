from __future__ import annotations

import socket

from .env import _raise, pick

DEFAULT_BACKEND_PORT = 8200
DEFAULT_AUTH_PORT = 8201
DEFAULT_STORAGE_PORT = 8202
DEFAULT_PRACTICE_PORT = 8203
DEFAULT_CONTENT_PORT = 8204
DEFAULT_STUDY_PORT = 8205
DEFAULT_LIBRARY_PORT = 8206
DEFAULT_ADMIN_PORT = 8207


def port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            sock.listen(1)
        except OSError:
            return False
    return True


def find_available_port(start: int, host: str = "127.0.0.1", max_tries: int = 200) -> int:
    found = next(filter(lambda port: port_available(port, host), range(start, start + max_tries)), None)
    return pick(
        found is None,
        lambda: _raise(RuntimeError(f"No available port found from {start} to {start + max_tries - 1}")),
        lambda: found,
    )


def allocate_backend_port(requested: int | None = None) -> int:
    def _requested() -> int:
        pick(
            not port_available(requested),
            lambda: _raise(RuntimeError(f"Port {requested} is already in use.")),
            lambda: None,
        )
        return requested

    return pick(requested is not None, _requested, lambda: find_available_port(DEFAULT_BACKEND_PORT))


def allocate_auth_port() -> int:
    return find_available_port(DEFAULT_AUTH_PORT)


def allocate_storage_port() -> int:
    return find_available_port(DEFAULT_STORAGE_PORT)


def allocate_practice_port() -> int:
    return find_available_port(DEFAULT_PRACTICE_PORT)


def allocate_content_port() -> int:
    return find_available_port(DEFAULT_CONTENT_PORT)


def allocate_study_port() -> int:
    return find_available_port(DEFAULT_STUDY_PORT)


def allocate_library_port() -> int:
    return find_available_port(DEFAULT_LIBRARY_PORT)


def allocate_admin_port() -> int:
    return find_available_port(DEFAULT_ADMIN_PORT)
