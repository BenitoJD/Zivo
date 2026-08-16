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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.engine_runtime import Pred, Rule, apply, first_match, pick  # noqa: E402

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


def _raise(exc: BaseException) -> None:
    raise exc


def _rm(path: Path) -> None:
    apply(
        first_match(
            (
                Rule(when=(Pred("isdir", "truthy"),), action="dir"),
                Rule(when=(Pred("exists", "truthy"),), action="file"),
                Rule(when=(), action="skip"),
            ),
            {"isdir": path.is_dir(), "exists": path.exists()},
        ).action,
        {
            "dir": lambda: shutil.rmtree(path),
            "file": lambda: path.unlink(),
            "skip": lambda: None,
        },
    )


def _module_path(root: Path, dotted: str) -> Path | None:
    parts = dotted.split(".")
    as_file = root.joinpath(*parts).with_suffix(".py")
    as_pkg = root.joinpath(*parts) / "__init__.py"
    return pick(
        as_file.is_file(),
        lambda: as_file,
        lambda: pick(as_pkg.is_file(), lambda: as_pkg, lambda: None),
    )


def _file_to_module(root: Path, path: Path) -> str | None:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    return pick(
        rel.suffix != ".py",
        lambda: None,
        lambda: _parts_to_module(list(rel.with_suffix("").parts)),
    )


def _parts_to_module(parts: list[str]) -> str | None:
    parts = pick(parts[-1] == "__init__", lambda: parts[:-1], lambda: parts)
    return pick(not parts, lambda: None, lambda: ".".join(parts))


def _walk_imports(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    pkg_parts = module.split(".")
    current_pkg = pick(path.name == "__init__.py", lambda: module, lambda: ".".join(pkg_parts[:-1]))

    def _on_import(node: ast.Import) -> None:
        for alias in node.names:
            found.add(alias.name)

    def _on_from(node: ast.ImportFrom) -> None:
        def _relative() -> None:
            base_parts = pick(bool(current_pkg), lambda: current_pkg.split("."), lambda: [])
            parent = pick(
                node.level > len(base_parts) + 1,
                lambda: "",
                lambda: ".".join(base_parts[: len(base_parts) - node.level + 1]),
            )
            abs_mod = pick(
                bool(node.module),
                lambda: pick(bool(parent), lambda: f"{parent}.{node.module}", lambda: node.module),
                lambda: parent,
            )

            def _add() -> None:
                found.add(abs_mod)

                def _aliases() -> None:
                    for alias in node.names:
                        pick(
                            alias.name == "*",
                            lambda: None,
                            lambda: found.add(f"{abs_mod}.{alias.name}"),
                        )

                pick(bool(node.module or node.names), _aliases, lambda: None)

            pick(bool(abs_mod), _add, lambda: None)

        def _absolute() -> None:
            found.add(node.module)
            for alias in node.names:
                pick(
                    alias.name == "*",
                    lambda: None,
                    lambda: found.add(f"{node.module}.{alias.name}"),
                )

        pick(bool(node.level), _relative, lambda: pick(bool(node.module), _absolute, lambda: None))

    for node in ast.walk(tree):
        pick(
            isinstance(node, ast.Import),
            lambda n=node: _on_import(n),
            lambda n=node: pick(isinstance(n, ast.ImportFrom), lambda: _on_from(n), lambda: None),
        )
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
        pick(
            not path.is_file(),
            lambda r=rel: _raise(SystemExit(f"slim: missing entrypoint {r}")),
            lambda: None,
        )
        mod = _file_to_module(root, path)
        queue.append(pick(bool(mod), lambda: mod, lambda r=rel: r.replace("/", ".").removesuffix(".py")))

    seen: set[str] = set()
    kept: set[Path] = set()

    while queue:
        dotted = queue.pop()

        def _visit() -> None:
            seen.add(dotted)
            path = _module_path(root, dotted)

            def _missing() -> None:
                parent = pick("." in dotted, lambda: dotted.rsplit(".", 1)[0], lambda: "")
                pick(bool(parent) and parent not in seen, lambda: queue.append(parent), lambda: None)

            def _keep() -> None:
                kept.add(path)
                for imported in _walk_imports(path, dotted):
                    pick(
                        _is_local_module(imported) and imported not in seen,
                        lambda name=imported: queue.append(name),
                        lambda: None,
                    )

            pick(path is None, _missing, _keep)

        pick(dotted in seen, lambda: None, _visit)
    return kept


def forbidden_leftovers(root: Path, profile: str) -> list[str]:
    """Paths that must never ship in this process image."""
    leftovers: list[str] = []
    own_pkg = OWN_HTTP_PACKAGE.get(profile)
    own_main = pick(profile in OWN_HTTP_PACKAGE, lambda: f"{profile}_main.py", lambda: None)

    for name in FORBIDDEN_DIRS:
        pick((root / name).exists(), lambda n=name: leftovers.append(n), lambda: None)

    for pkg in HTTP_PACKAGES:
        pick(
            pkg != own_pkg and (root / pkg).exists(),
            lambda p=pkg: leftovers.append(p),
            lambda: None,
        )

    for main in HTTP_MAINS:
        pick(
            main != own_main and (root / main).exists(),
            lambda m=main: leftovers.append(m),
            lambda: None,
        )

    api_dir = root / "app" / "api"

    def _scan_api() -> None:
        for child in api_dir.iterdir():
            pick(
                child.name in PRODUCT_ROUTER_FILES,
                lambda c=child: leftovers.append(f"app/api/{c.name}"),
                lambda: None,
            )

    pick(api_dir.is_dir(), _scan_api, lambda: None)
    pick(
        profile in {"worker", "migrate"} and (root / "app" / "api").exists(),
        lambda: leftovers.append("app/api"),
        lambda: None,
    )
    pick(
        (root / "app" / "main.py").exists(),
        lambda: leftovers.append("app/main.py"),
        lambda: None,
    )
    return leftovers


def _drop_unreached_app_modules(root: Path, keep_files: set[Path]) -> None:
    app_dir = root / "app"

    def _drop() -> None:
        for path in sorted(app_dir.rglob("*.py"), reverse=True):
            pick(path not in keep_files, path.unlink, lambda: None)
        for dirpath in sorted(filter(Path.is_dir, app_dir.rglob("*")), reverse=True):
            try:
                next(dirpath.iterdir())
            except StopIteration:
                dirpath.rmdir()

    pick(app_dir.is_dir(), _drop, lambda: None)


def slim(root: Path, profile: str) -> None:
    pick(
        profile not in API_KEEP,
        lambda: _raise(SystemExit(f"unknown profile {profile!r}; choose from {sorted(API_KEEP)}")),
        lambda: None,
    )

    api_dir = root / "app" / "api"
    keep = API_KEEP[profile]

    def _trim_api() -> None:
        pick(not keep, lambda: _rm(api_dir), lambda: None)
        pick(
            bool(keep),
            lambda: [
                pick(child.name not in keep, lambda c=child: _rm(c), lambda: None)
                for child in api_dir.iterdir()
            ],
            lambda: None,
        )

    pick(api_dir.is_dir(), _trim_api, lambda: None)
    _rm(root / "app" / "main.py")
    pick(profile != "migrate", lambda: (_rm(root / "alembic"), _rm(root / "alembic.ini"), _rm(root / "schema")), lambda: None)
    pick(
        profile != "worker",
        lambda: [_rm(root / name) for name in WORKER_ENTRYPOINTS],
        lambda: None,
    )

    own_pkg = OWN_HTTP_PACKAGE.get(profile)
    own_main = pick(bool(own_pkg), lambda: f"{profile}_main.py", lambda: None)
    for pkg in HTTP_PACKAGES:
        pick(pkg != own_pkg, lambda p=pkg: _rm(root / p), lambda: None)
    for main in HTTP_MAINS:
        pick(main != own_main, lambda m=main: _rm(root / m), lambda: None)

    for name in FORBIDDEN_DIRS:
        _rm(root / name)
    for cache in list(root.rglob("__pycache__")):
        _rm(cache)
    for pyc in list(root.rglob("*.pyc")):
        _rm(pyc)
    for name in ALWAYS_DROP_ROOT_FILES:
        _rm(root / name)

    scripts = root / "scripts"

    def _trim_scripts() -> None:
        def _migrate_scripts() -> None:
            for child in scripts.iterdir():
                pick(child.name not in MIGRATE_KEEP_SCRIPTS, lambda c=child: _rm(c), lambda: None)

        pick(profile == "migrate", _migrate_scripts, lambda: _rm(scripts))

    pick(scripts.is_dir(), _trim_scripts, lambda: None)

    entrypoints = ENTRYPOINTS[profile]

    def _migrate_extra() -> tuple[str, ...]:
        versions = root / "alembic" / "versions"
        extra = pick(
            versions.is_dir(),
            lambda: tuple(
                str(path.relative_to(root))
                for path in sorted(filter(Path.is_file, versions.glob("*.py")))
            ),
            lambda: (),
        )
        return entrypoints + extra

    entrypoints = pick(profile == "migrate", _migrate_extra, lambda: entrypoints)
    keep_files = reachable_modules(root, entrypoints)
    _drop_unreached_app_modules(root, keep_files)
    leftovers = forbidden_leftovers(root, profile)
    pick(
        bool(leftovers),
        lambda: _raise(SystemExit(f"slim {profile}: forbidden leftovers remain: {leftovers}")),
        lambda: None,
    )
    print(f"slimmed {root} as {profile}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=sorted(API_KEEP))
    parser.add_argument("--root", default=".", help="Copied backend tree (default cwd)")
    args = parser.parse_args()
    slim(Path(args.root).resolve(), args.profile)
    return 0


def _cli() -> None:
    raise SystemExit(main())


pick(__name__ == "__main__", _cli, lambda: None)
