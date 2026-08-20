from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

KEEP = re.compile(r"^#\s*(noqa|type:|ty:|pragma|pylint|mypy|ruff:|fmt:|isort:|!)")


def strip_comments(src: str) -> str:
    lines = src.splitlines(keepends=True)
    cuts: dict[int, list[tuple[int, int]]] = {}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            continue
        if KEEP.match(tok.string.strip()):
            continue
        cuts.setdefault(tok.start[0] - 1, []).append((tok.start[1], tok.end[1]))
    out = []
    for i, line in enumerate(lines):
        if i in cuts:
            for a, b in sorted(cuts[i], reverse=True):
                line = line[:a] + line[b:]
            if not line.strip():
                continue
            line = line.rstrip() + "\n"
        out.append(line)
    return "".join(out)


def _is_bare_string(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def strip_docstrings(src: str) -> str:
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    drop: set[int] = set()
    inject: dict[int, str] = {}
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if not isinstance(body, list) or not body:
                continue
            bare = [s for s in body if _is_bare_string(s)]
            if not bare:
                continue
            for stmt in bare:
                for i in range(stmt.lineno - 1, stmt.end_lineno):
                    drop.add(i)
            if len(bare) == len(body) and not isinstance(node, ast.Module):
                first = bare[0]
                inject[first.lineno - 1] = f"{' ' * first.col_offset}pass\n"
    out = []
    for i, line in enumerate(lines):
        if i in inject:
            out.append(inject[i])
            continue
        if i in drop:
            continue
        out.append(line)
    return "".join(out)


def tidy(src: str) -> str:
    src = re.sub(r"\n{4,}", "\n\n\n", src)
    src = re.sub(r"\A\n+", "", src)
    src = re.sub(r"\n{2,}\Z", "\n", src)
    return src


def process(path: Path) -> tuple[int, int]:
    original = path.read_text(encoding="utf-8")
    try:
        step = strip_comments(original)
        step = strip_docstrings(step)
        step = tidy(step)
        ast.parse(step)
    except (SyntaxError, tokenize.TokenError) as exc:
        print(f"SKIP {path}: {type(exc).__name__} {exc}")
        return 0, 0
    if step != original:
        path.write_text(step, encoding="utf-8")
    return len(original.splitlines()), len(step.splitlines())


def main(roots: list[str]) -> None:
    before = after = 0
    files = []
    for r in roots:
        files += [p for p in Path(r).rglob("*.py") if ".venv" not in str(p) and "__pycache__" not in str(p)]
    for f in sorted(set(files)):
        if f.name == "strip_prose.py":
            continue
        b, a = process(f)
        before += b
        after += a
    print(f"python: {before} -> {after} lines ({before - after} removed) across {len(set(files))} files")


if __name__ == "__main__":
    main(sys.argv[1:] or ["backend", "scripts", "bench"])
