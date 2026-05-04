#!/usr/bin/env python3
"""Stage 02 — DNS resolver.

Reads data/seeds.jsonl, resolves each unique domain via 1.1.1.1/8.8.8.8, drops
NXDOMAIN/parking IPs, tags CDN, and emits data/live.jsonl.

Idempotent: skips domains already in live.jsonl unless --force.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import data_dir  # noqa: E402
from lib.dns_utils import cdn_for_ip, resolve  # noqa: E402
from lib.domain import is_parking_ip  # noqa: E402
from lib.jsonl import append_record, iter_records, read_existing_keys  # noqa: E402

SEEDS_PATH = data_dir() / "seeds.jsonl"
LIVE_PATH = data_dir() / "live.jsonl"


async def resolve_one(domain: str, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        try:
            res = await resolve(domain)
        except Exception:
            return None
        if not res:
            return None
        ip = res.get("ip")
        if is_parking_ip(ip):
            return None
        return {
            "domain": domain,
            "ip": ip,
            "a": res.get("a") or [],
            "aaaa": res.get("aaaa") or [],
            "cname": res.get("cname"),
            "ns": res.get("ns") or [],
            "cdn": cdn_for_ip(ip),
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }


async def run(force: bool, limit: int | None, concurrency: int) -> int:
    if not SEEDS_PATH.exists():
        print(f"[dns] no seeds at {SEEDS_PATH}; run stage 01 first", file=sys.stderr)
        return 0
    seen = set() if force else read_existing_keys(LIVE_PATH, "domain")
    domains: list[str] = []
    for rec in iter_records(SEEDS_PATH):
        d = rec.get("domain")
        if isinstance(d, str) and d not in seen and d not in domains:
            domains.append(d)
            if limit and len(domains) >= limit:
                break
    print(f"[dns] resolving {len(domains)} unresolved domains (concurrency={concurrency})",
          file=sys.stderr)
    sem = asyncio.Semaphore(concurrency)
    written = 0
    tasks = [resolve_one(d, sem) for d in domains]
    for fut in asyncio.as_completed(tasks):
        rec = await fut
        if rec is None:
            continue
        append_record(LIVE_PATH, rec)
        written += 1
        if written % 100 == 0:
            print(f"[dns] resolved {written}", file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None, help="Cap on domains for testing")
    p.add_argument("--concurrency", type=int, default=500)
    args = p.parse_args()
    n = asyncio.run(run(args.force, args.limit, args.concurrency))
    print(f"[dns] wrote {n} records to {LIVE_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
