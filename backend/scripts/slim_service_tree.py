#!/usr/bin/env python3
"""Strip a copied backend tree down to one process's code.

Used by Dockerfiles after COPY of that process's files plus ``backend/app``.
Profiles drop FastAPI route modules the process does not serve, sibling HTTP
packages, worker/migrate entrypoints it does not run, and ``app.*`` modules the
process never imports (AST reachability from the process entrypoints).
"""

from __future__ import annotations

import argparse
import ast
import shutil
from pathlib import Path

API_KEEP: dict[str, set[str]] = {
    "worker": set(),
    "migrate": set(),
    "practice": {"__init__.py", "health.py"},
    "content": {"__init__.py", "health.py"},
    "study": {"__init__.py", "health.py"},
    "library": {"__init__.py", "health.py"},
    "admin": {"__init__.py", "health.py"},
}

OWN_HTTP_PACKAGE = {
    "practice": "practice_api",
    "content": "content_api",
    "study": "study_api",
    "library": "library_api",
    "admin": "admin_api",
}

ENTRYPOINTS = {
    "worker": (
        "run_eta_worker_async.py",
        "run_eta_worker_cpu.py",
        "run_newspaper_ingest.py",
    ),
    "migrate": ("alembic/env.py",),
    "practice": ("practice_main.py",),
    "content": ("content_main.py",),
    "study": ("study_main.py",),
    "library": ("library_main.py",),
    "admin": ("admin_main.py",),
}

HTTP_PACKAGES = (
    "practice_api",
    "content_api",
    "study_api",
    "library_api",
    "admin_api",
)

HTTP_MAINS = (
    "practice_main.py",
    "content_main.py",
    "study_main.py",
    "library_main.py",
    "admin_main.py",
)

WORKER_ENTRYPOINTS = (
    "run_eta_worker_async.py",
    "run_eta_worker_cpu.py",
    "run_newspaper_ingest.py",
)

FORBIDDEN_DIRS = (
    "frontend",
    ".git",
    "docs",
    "infra",
    "landing",
    "graphify-out",
    "auth",
    "storage",
    "workers",
    "tests",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
)

PRODUCT_ROUTER_FILES = (
    "access.py",
    "activities.py",
    "artifacts.py",
    "assertions.py",
    "audiobook.py",
    "chat.py",
    "coding.py",
    "debug.py",
    "documents.py",
    "guest.py",
    "learn.py",
    "mcq.py",
    "models.py",
    "newspaper.py",
    "newspaper_admin.py",
    "offline.py",
    "practice.py",
    "progress.py",
    "reference.py",
    "seo_learn.py",
    "sources.py",
    "study.py",
    "system_design.py",
    "topics.py",
)

ALWAYS_DROP_ROOT_FILES = (
    "requirements-dev.txt",
    "Dockerfile",
    "ruff.toml",
    ".dockerignore",
    "README.md",
)

MIGRATE_KEEP_SCRIPTS = {"run_alembic_with_lock.py", "seed_question_vocab.py"}

LOCAL_ROOTS = ("app",) + HTTP_PACKAGES


def _rm(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _module_path(root: Path, dotted: str) -> Path | None:
    parts = dotted.split(".")
    as_file = root.joinpath(*parts).with_suffix(".py")
    as_pkg = root.joinpath(*parts) / "__init__.py"
    if as_file.is_file():
        return as_file
    if as_pkg.is_file():
        return as_pkg
    return None


def _file_to_module(root: Path, path: Path) -> str | None:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    if rel.suffix != ".py":
        return None
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


def _walk_imports(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    pkg_parts = module.split(".")
    if path.name == "__init__.py":
        current_pkg = module
    else:
        current_pkg = ".".join(pkg_parts[:-1])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = current_pkg.split(".") if current_pkg else []
                if node.level > len(base_parts) + 1:
                    parent = ""
                else:
                    parent = ".".join(base_parts[: len(base_parts) - node.level + 1])
                if node.module:
                    abs_mod = f"{parent}.{node.module}" if parent else node.module
                else:
                    abs_mod = parent
                if abs_mod:
                    found.add(abs_mod)
                    if node.module or node.names:
                        for alias in node.names:
                            if alias.name == "*":
                                continue
                            found.add(f"{abs_mod}.{alias.name}")
            elif node.module:
                found.add(node.module)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    found.add(f"{node.module}.{alias.name}")
    return found


def _is_local_module(name: str) -> bool:
    return name == "app" or name.startswith("app.") or any(
        name == pkg or name.startswith(f"{pkg}.") for pkg in HTTP_PACKAGES
    )


def reachable_modules(root: Path, entrypoints: tuple[str, ...]) -> set[Path]:
    """Return Python files under root reachable by static imports from entrypoints."""
    queue: list[str] = []
    for rel in entrypoints:
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"slim: missing entrypoint {rel}")
        mod = _file_to_module(root, path)
        if mod:
            queue.append(mod)
        else:
            queue.append(rel.replace("/", ".").removesuffix(".py"))

    seen: set[str] = set()
    kept: set[Path] = set()

    while queue:
        dotted = queue.pop()
        if dotted in seen:
            continue
        seen.add(dotted)
        path = _module_path(root, dotted)
        if path is None:
            # ``from app.models import Account``: Account is a symbol, not a module.
            parent = dotted.rsplit(".", 1)[0] if "." in dotted else ""
            if parent and parent not in seen:
                queue.append(parent)
            continue
        kept.add(path)
        for imported in _walk_imports(path, dotted):
            if _is_local_module(imported) and imported not in seen:
                queue.append(imported)
    return kept


def forbidden_leftovers(root: Path, profile: str) -> list[str]:
    """Paths that must never ship in this process image."""
    leftovers: list[str] = []
    own_pkg = OWN_HTTP_PACKAGE.get(profile)
    own_main = f"{profile}_main.py" if profile in OWN_HTTP_PACKAGE else None

    for name in FORBIDDEN_DIRS:
        if name == "tests" and profile == "migrate":
            # dropped structurally; still forbidden if present
            pass
        if (root / name).exists():
            leftovers.append(name)

    for pkg in HTTP_PACKAGES:
        if pkg == own_pkg:
            continue
        if (root / pkg).exists():
            leftovers.append(pkg)

    for main in HTTP_MAINS:
        if main == own_main:
            continue
        if (root / main).exists():
            leftovers.append(main)

    api_dir = root / "app" / "api"
    if api_dir.is_dir():
        for child in api_dir.iterdir():
            if child.name in PRODUCT_ROUTER_FILES:
                leftovers.append(f"app/api/{child.name}")

    if profile in {"worker", "migrate"} and (root / "app" / "api").exists():
        leftovers.append("app/api")
    if (root / "app" / "main.py").exists():
        leftovers.append("app/main.py")

    return leftovers


def _drop_unreached_app_modules(root: Path, keep_files: set[Path]) -> None:
    app_dir = root / "app"
    if not app_dir.is_dir():
        return
    for path in sorted(app_dir.rglob("*.py"), reverse=True):
        if path not in keep_files:
            path.unlink()
    for dirpath in sorted((p for p in app_dir.rglob("*") if p.is_dir()), reverse=True):
        try:
            next(dirpath.iterdir())
        except StopIteration:
            dirpath.rmdir()


def slim(root: Path, profile: str) -> None:
    if profile not in API_KEEP:
        raise SystemExit(f"unknown profile {profile!r}; choose from {sorted(API_KEEP)}")

    api_dir = root / "app" / "api"
    keep = API_KEEP[profile]
    if api_dir.is_dir():
        if not keep:
            _rm(api_dir)
        else:
            for child in api_dir.iterdir():
                if child.name not in keep:
                    _rm(child)

    _rm(root / "app" / "main.py")
    if profile != "migrate":
        _rm(root / "alembic")
        _rm(root / "alembic.ini")
        _rm(root / "schema")

    if profile != "worker":
        for name in WORKER_ENTRYPOINTS:
            _rm(root / name)

    own_pkg = OWN_HTTP_PACKAGE.get(profile)
    own_main = f"{profile}_main.py" if own_pkg else None
    for pkg in HTTP_PACKAGES:
        if pkg != own_pkg:
            _rm(root / pkg)
    for main in HTTP_MAINS:
        if main != own_main:
            _rm(root / main)

    for name in FORBIDDEN_DIRS:
        _rm(root / name)
    for cache in list(root.rglob("__pycache__")):
        _rm(cache)
    for pyc in list(root.rglob("*.pyc")):
        _rm(pyc)

    for name in ALWAYS_DROP_ROOT_FILES:
        _rm(root / name)

    scripts = root / "scripts"
    if scripts.is_dir():
        if profile == "migrate":
            for child in scripts.iterdir():
                if child.name not in MIGRATE_KEEP_SCRIPTS:
                    _rm(child)
        else:
            _rm(scripts)

    entrypoints = ENTRYPOINTS[profile]
    if profile == "migrate":
        versions = root / "alembic" / "versions"
        extra = tuple(
            str(path.relative_to(root))
            for path in sorted(versions.glob("*.py"))
            if path.is_file()
        ) if versions.is_dir() else ()
        entrypoints = entrypoints + extra
    keep_files = reachable_modules(root, entrypoints)
    _drop_unreached_app_modules(root, keep_files)

    leftovers = forbidden_leftovers(root, profile)
    if leftovers:
        raise SystemExit(f"slim {profile}: forbidden leftovers remain: {leftovers}")

    print(f"slimmed {root} as {profile}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=sorted(API_KEEP))
    parser.add_argument("--root", default=".", help="Copied backend tree (default cwd)")
    args = parser.parse_args()
    slim(Path(args.root).resolve(), args.profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
