from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_CHUNK = 1024 * 1024


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def inline_defs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                return walk(json.loads(json.dumps(defs[name])))
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def format_clock(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


__all__ = ["file_sha256", "format_clock", "inline_defs"]


def _demo() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "x.bin"
        p.write_bytes(b"a" * (_CHUNK * 2 + 7))
        assert file_sha256(p) == hashlib.sha256(p.read_bytes()).hexdigest()

    nested = {
        "$defs": {"Inner": {"type": "integer"}},
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/Inner"}, "b": [{"$ref": "#/$defs/Inner"}]},
    }
    assert inline_defs(nested) == {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": [{"type": "integer"}]},
    }

    assert format_clock(0) == "00:00"
    assert format_clock(59.9) == "00:59"
    assert format_clock(61) == "01:01"
    assert format_clock(3600) == "60:00"
    print("utils demo tamam")


if __name__ == "__main__":
    _demo()
