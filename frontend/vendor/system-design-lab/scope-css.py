#!/usr/bin/env python3
"""Scope the Design Lab's stylesheets to the Zivo mount point.

Zivo mounts the vendored app inside `.bscope` (see VENDOR.md). Upstream CSS
was written for a page the lab owns outright, so bare element selectors
(``body``, ``button``, ``input``, ``*``) would restyle the whole product.
This script rewrites every selector that can match Zivo elements so it can
only match inside the mount:

- ``:root`` / ``:root[data-theme='dark']`` keep their custom properties
  global on purpose: vendored code reads the tokens off
  ``document.documentElement`` (textMetrics, Canvas, imageExport). Any real
  declaration in a rooted block (upstream has ``color-scheme: dark``) is
  split out into a ``.bscope`` mirror block; the Zivo mount mirrors the
  ``data-theme`` attribute so the mirror follows the lab's theme switch.
- ``html`` / ``body`` / ``#root`` map onto ``.bscope`` itself: the wrapper
  *is* the lab's page root inside the Zivo shell.
- bare elements and element-compounds get the descendant prefix ``.bscope ``
  (``button`` -> ``.bscope button``, ``*`` -> ``.bscope, .bscope *``).
- class / id selectors are left alone; Zivo does not use the ``pal-``-style
  namespaces upstream picked (checked against Zivo's class inventory when
  this ran).

Run from this directory after re-copying an upstream snapshot:

    python3 scope-css.py

Idempotent: a selector already scoped (starts with ``.bscope``) is left as
found, so a second pass is a no-op.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCOPE = ".bscope"
# :root blocks only declare custom properties; vendored code reads them off
# documentElement, so they must stay global.
ROOTED_PREFIX = ":root"
PAGE_ROOT_SELECTOR_RE = re.compile(r"^(html|body|#root)$")

CSS_FILES = sorted(HERE.glob("src/**/*.css"))


def strip_comment_spans(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def split_top_level(selector_list: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in selector_list:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def split_statements(body: str) -> list[str]:
    """Split declarations on `;`, never inside comments or strings."""
    parts: list[str] = []
    buf: list[str] = []
    in_comment = False
    in_string: str = ""
    i = 0
    while i < len(body):
        ch = body[i]
        if in_comment:
            buf.append(ch)
            if body.startswith("*/", i):
                buf.append("/")
                i += 2
                in_comment = False
                continue
            i += 1
            continue
        if in_string:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(body):
                buf.append(body[i + 1])
                i += 2
                continue
            if ch == in_string:
                in_string = ""
            i += 1
            continue
        if body.startswith("/*", i):
            in_comment = True
            buf.append(ch)
            i += 1
            continue
        if ch in {"'", '"'}:
            in_string = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            parts.append("".join(buf) + ";")
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if "".join(buf).strip():
        parts.append("".join(buf))
    # Stripping keeps a re-split of already-split output stable (pass 2 of an
    # idempotency check must not grow blank lines inside rooted blocks).
    return [p.strip() for p in parts if p.strip()]


def is_var_declaration(statement: str) -> bool:
    without_comments = re.sub(r"/\*.*?\*/", "", statement, flags=re.S).strip()
    return re.match(r"^--[a-z0-9-]+\s*:", without_comments) is not None


def is_comment_only(statement: str) -> bool:
    return strip_comment_spans(statement).strip() == ""


def split_rooted_block(selector: str, body: str) -> tuple[str, str]:
    """Split a rooted block into (global vars block, scoped mirror block).

    Comments stay with the global vars block: they document the tokens, and
    upstream has exactly one real declaration in a rooted block.
    """
    var_lines: list[str] = []
    rest: list[str] = []
    for statement in split_statements(body):
        if is_var_declaration(statement) or is_comment_only(statement):
            var_lines.append(statement if statement.endswith(";") else statement)
        else:
            rest.append(statement)
    if selector == ROOTED_PREFIX:
        mirror = SCOPE
    else:
        mirror = selector.replace(ROOTED_PREFIX, SCOPE, 1)
    vars_block = (selector + "{" + "\n".join(var_lines) + "}") if var_lines else ""
    rest_block = (mirror + "{" + "\n".join(rest) + "}") if rest else ""
    return vars_block, rest_block


def scope_one_selector(selector: str) -> list[str]:
    """Scoped replacements for one comma-separated selector part."""
    if selector.startswith(SCOPE):
        return [selector]
    if PAGE_ROOT_SELECTOR_RE.match(selector):
        return [SCOPE]
    if selector == ROOTED_PREFIX or selector.startswith(ROOTED_PREFIX + "["):
        return [selector]
    if selector == "*":
        # The wrapper needs the reset too, so it joins its own descendants.
        return [SCOPE, SCOPE + " *"]
    head = re.match(r"^([a-zA-Z][a-zA-Z0-9-]*|\*|\[|::?)", selector)
    if head:
        return [f"{SCOPE} {selector}"]
    return [selector]


def scope_selector_list(selector_list: str) -> str:
    scoped: list[str] = []
    for raw in split_top_level(selector_list):
        scoped += scope_one_selector(raw.strip())
    seen: list[str] = []
    for s in scoped:
        if s not in seen:
            seen.append(s)
    return ",\n".join(seen)


VERBATIM_AT = {"font-face", "keyframes", "-webkit-keyframes", "property", "font-palette-values"}


def transform(text: str) -> tuple[str, list[str]]:
    out: list[str] = []
    changed: list[str] = []
    i = 0
    n = len(text)

    while i < n:
        ws = re.match(r"\s+", text[i:])
        if ws:
            out.append(ws.group(0))
            i += ws.end()
            continue
        comment = re.compile(r"/\*.*?\*/", re.S).match(text, i)
        if comment:
            out.append(comment.group(0))
            i = comment.end()
            continue
        brace = text.find("{", i)
        if brace < 0:
            out.append(text[i:])
            break
        end = match_block_end(text, brace)
        body = text[brace + 1:end - 1]
        selector = " ".join(strip_comment_spans(text[i:brace]).split())
        if selector.startswith("@"):
            name = selector.split()[0][1:]
            if name in VERBATIM_AT:
                out.append(text[i:end])
            else:
                inner, inner_changed = transform(body)
                out.append(text[i:brace + 1] + inner + "}")
                changed += inner_changed
            i = end
            continue
        if selector.startswith(ROOTED_PREFIX):
            vars_block, rest_block = split_rooted_block(selector, body)
            combined = (vars_block + "\n" + rest_block) if vars_block and rest_block else (vars_block + rest_block)
            out.append(combined)
            if rest_block:
                changed.append(rest_block.split("{")[0])
            i = end
            continue
        scoped = scope_selector_list(selector)
        if scoped != selector:
            changed.append(scoped)
        out.append(scoped + "{" + body + "}")
        i = end
    return "".join(out), changed


def match_block_end(text: str, open_brace: int) -> int:
    depth = 0
    for j in range(open_brace, len(text)):
        ch = text[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    raise ValueError("unbalanced braces")


def main() -> int:
    total = 0
    for path in CSS_FILES:
        original = path.read_text(encoding="utf-8")
        scoped, changed = transform(original)
        path.write_text(scoped, encoding="utf-8")
        rel = path.relative_to(HERE)
        print(f"{rel}: {len(changed)} selector lists scoped")
        total += len(changed)
    print(f"total selector lists touched: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
