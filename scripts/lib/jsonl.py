"""JSONL helpers — append-only, idempotent, parallel-safe."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator

import orjson


def read_existing_keys(path: str | Path, key: str = "domain") -> set[str]:
    """Return the set of values seen at `record[key]` in an existing JSONL file.

    Used by every stage to skip records already processed (idempotency).
    Returns an empty set if the file doesn't exist or is empty.
    """
    p = Path(path)
    if not p.exists():
        return set()
    seen: set[str] = set()
    with p.open("rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            v = rec.get(key)
            if isinstance(v, str):
                seen.add(v)
    return seen


def read_existing_pairs(path: str | Path, key1: str, key2: str) -> set[tuple[str, str]]:
    """Like read_existing_keys but composite key — used by stage 01 for (domain, source)."""
    p = Path(path)
    if not p.exists():
        return set()
    seen: set[tuple[str, str]] = set()
    with p.open("rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            v1, v2 = rec.get(key1), rec.get(key2)
            if isinstance(v1, str) and isinstance(v2, str):
                seen.add((v1, v2))
    return seen


def append_record(path: str | Path, record: dict[str, Any]) -> None:
    """Atomically append one JSON record as a line.

    POSIX guarantees writes <= PIPE_BUF (4096B) to a file opened with O_APPEND
    are atomic with respect to other appenders. orjson.dumps gives compact bytes;
    a single record is well under 4KB unless body_sample_b64 is large (16KB max).
    For larger records we accept the rare interleave risk; idempotent re-runs
    will skip duplicates.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = orjson.dumps(record) + b"\n"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def iter_records(path: str | Path) -> Iterator[dict[str, Any]]:
    """Stream-read records from a JSONL file. Skips malformed lines."""
    p = Path(path)
    if not p.exists():
        return
    with p.open("rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield orjson.loads(line)
            except orjson.JSONDecodeError:
                continue


def count_records(path: str | Path) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    with p.open("rb") as f:
        return sum(1 for line in f if line.strip())
