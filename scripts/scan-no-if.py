#!/usr/bin/env python3
"""Fail when first-party Python, TypeScript, or JavaScript still contains if-conditions."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".next",
    "dist",
    "build",
    "graphify-out",
    ".cursor",
    ".mypy_cache",
    ".pytest_cache",
    "coverage",
}

SKIP_PATH_PARTS: tuple[str, ...] = ()

TS_IF_RE = re.compile(r"(?:^|[^A-Za-z0-9_])if\s*\(")
TS_TERNARY_RE = re.compile(r"[^=]\?[^?:\n]{1,120}:")
TS_AND_JSX_RE = re.compile(r"\{\s*[^}]{0,80}&&")


def _choose(flag: bool, when_true, when_false):
    return {True: when_true, False: when_false}[bool(flag)]


def _pick(flag: bool, when_true, when_false):
    return {True: when_true, False: when_false}[bool(flag)]()


def _skipped(path: Path) -> bool:
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    return bool(set(path.parts) & SKIP_DIR_NAMES) or any(
        part in rel for part in SKIP_PATH_PARTS
    )


class IfFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    def visit_If(self, node: ast.If) -> None:
        self.hits.append((node.lineno, "if"))
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.hits.append((node.lineno, "ternary"))
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.hits += [(n.lineno, "comprehension-if") for n in node.ifs]
        self.generic_visit(node)


def scan_python(path: Path) -> list[dict[str, object]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    finder = IfFinder()
    finder.visit(tree)
    rel = str(path.relative_to(ROOT))
    return [{"file": rel, "line": line, "kind": kind} for line, kind in finder.hits]


def scan_typescript(path: Path) -> list[dict[str, object]]:
    rel = str(path.relative_to(ROOT))
    rows: list[dict[str, object]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        comment = (
            stripped.startswith("//")
            or stripped.startswith("*")
            or stripped.startswith("/*")
        )
        live = not comment
        rows += _choose(
            live and bool(TS_IF_RE.search(raw)),
            [{"file": rel, "line": lineno, "kind": "if"}],
            [],
        )
        rows += _choose(
            live and bool(TS_TERNARY_RE.search(raw)) and ("?:" not in raw),
            [{"file": rel, "line": lineno, "kind": "ternary"}],
            [],
        )
        rows += _choose(
            live and bool(TS_AND_JSX_RE.search(raw)),
            [{"file": rel, "line": lineno, "kind": "jsx-and"}],
            [],
        )
    return rows


def iter_files() -> list[Path]:
    return list(filter(lambda p: p.is_file() and not _skipped(p), ROOT.rglob("*")))


def main() -> int:
    scanners = {
        ".py": scan_python,
        ".ts": scan_typescript,
        ".tsx": scan_typescript,
        ".js": scan_typescript,
        ".jsx": scan_typescript,
        ".mjs": scan_typescript,
    }
    hits: list[dict[str, object]] = []
    for path in iter_files():
        hits += scanners.get(path.suffix, lambda _p: [])(path)
    shown = hits[:500]
    print(json.dumps({"count": len(hits), "hits": shown, "truncated": max(0, len(hits) - 500)}, indent=2))
    print(f"no-if scanner: {len(hits)} remaining", file=sys.stderr)
    return int(bool(hits))


def _cli() -> None:
    raise SystemExit(main())


_pick(__name__ == "__main__", _cli, lambda: None)
