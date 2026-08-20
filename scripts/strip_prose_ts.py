from __future__ import annotations

import re
import sys
from pathlib import Path

KEEP = re.compile(r"^\s*//\s*(@ts-|eslint|prettier|biome|noqa)")
REGEX_OK_BEFORE = set("(,=:[!&|?{};+-*%~^<>")
KEYWORDS_BEFORE = ("return", "typeof", "case", "in", "of", "new", "delete", "void", "throw", "do", "else", "yield", "await")


def last_significant(out: list[str]) -> str:
    for ch in reversed(out):
        if not ch.isspace():
            return ch
    return ""


def regex_allowed(out: list[str]) -> bool:
    ch = last_significant(out)
    if ch == "":
        return True
    if ch in REGEX_OK_BEFORE:
        return True
    tail = "".join(out).rstrip()
    return any(tail.endswith(k) for k in KEYWORDS_BEFORE)


def strip(src: str) -> str:
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if c in "\"'":
            q = c
            out.append(c)
            i += 1
            while i < n:
                out.append(src[i])
                if src[i] == "\\":
                    if i + 1 < n:
                        out.append(src[i + 1])
                        i += 2
                        continue
                elif src[i] == q:
                    i += 1
                    break
                i += 1
            continue

        if c == "`":
            out.append(c)
            i += 1
            while i < n:
                out.append(src[i])
                if src[i] == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                if src[i] == "`":
                    i += 1
                    break
                i += 1
            continue

        if c == "{" and nxt == "/" and i + 2 < n and src[i + 2] == "*":
            end = src.find("*/", i + 3)
            if end != -1:
                after = src.find("}", end + 2)
                between = src[end + 2:after] if after != -1 else "x"
                if after != -1 and between.strip() == "":
                    i = after + 1
                    while i < n and src[i] in " \t":
                        i += 1
                    if i < n and src[i] == "\n" and not "".join(out).split("\n")[-1].strip():
                        out = list("".join(out).rstrip(" \t"))
                        i += 1
                    continue

        if c == "/" and nxt == "/":
            line_end = src.find("\n", i)
            line_end = n if line_end == -1 else line_end
            if KEEP.match(src[i:line_end]):
                out.append(src[i:line_end])
                i = line_end
                continue
            prefix = "".join(out).split("\n")[-1]
            i = line_end
            if not prefix.strip():
                while out and out[-1] in " \t":
                    out.pop()
                if i < n:
                    i += 1
            else:
                while out and out[-1] in " \t":
                    out.pop()
            continue

        if c == "/" and nxt == "*":
            end = src.find("*/", i + 2)
            i = n if end == -1 else end + 2
            prefix = "".join(out).split("\n")[-1]
            if not prefix.strip():
                while out and out[-1] in " \t":
                    out.pop()
                while i < n and src[i] in " \t":
                    i += 1
                if i < n and src[i] == "\n":
                    i += 1
            continue

        if c == "/" and regex_allowed(out):
            j = i + 1
            ok = False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "/":
                    ok = True
                    break
                j += 1
            if ok:
                j += 1
                while j < n and src[j].isalpha():
                    j += 1
                out.append(src[i:j])
                i = j
                continue

        out.append(c)
        i += 1

    text = "".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n") + "\n"


def main(root: str) -> None:
    files = [p for p in Path(root).rglob("*.ts")] + [p for p in Path(root).rglob("*.tsx")]
    before = after = 0
    for f in sorted(files):
        src = f.read_text(encoding="utf-8")
        new = strip(src)
        before += len(src.splitlines())
        after += len(new.splitlines())
        if new != src:
            f.write_text(new, encoding="utf-8")
    print(f"ts: {before} -> {after} lines ({before - after} removed) across {len(files)} files")


if __name__ == "__main__":
    for r in sys.argv[1:] or ["frontend/src", "frontend/tests"]:
        main(r)
