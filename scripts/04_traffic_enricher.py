#!/usr/bin/env python3
"""Stage 04 — Traffic enricher.

Reads data/detections.jsonl filtered to score >= --min-score (default 70),
adds Tranco rank from the cached CSV and (optionally) Cloudflare Radar bucket.
Emits data/enriched.jsonl.

Idempotent: skips already-enriched domains unless --force.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir  # noqa: E402
from lib.http import polite_client  # noqa: E402
from lib.jsonl import append_record, iter_records, read_existing_keys  # noqa: E402

DETECTIONS_PATH = data_dir() / "detections.jsonl"
ENRICHED_PATH = data_dir() / "enriched.jsonl"


def load_tranco(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    out: dict[str, int] = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 2:
                continue
            try:
                rank = int(row[0])
            except ValueError:
                continue
            domain = row[1].strip().lower()
            if domain:
                out[domain] = rank
    return out


async def cf_radar_bucket(domain: str, token: str, cfg: Config) -> str | None:
    """Cloudflare Radar /radar/ranking/domain/{d} returns trend + top bucket."""
    url = f"https://api.cloudflare.com/client/v4/radar/ranking/domain/{domain}"
    headers = {"Authorization": f"Bearer {token}"}
    async with polite_client(
        user_agent=cfg.user_agent, rps_per_host=2.0, timeout=15, max_concurrent=4
    ) as c:
        try:
            resp = await c.client.get(url, headers=headers)
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        result = (data.get("result") or {}).get("details_0") or {}
        # Radar returns a rank like 12345; bucket it.
        rank = result.get("rank")
        if isinstance(rank, int):
            return _bucket(rank)
    return None


def _bucket(rank: int) -> str:
    if rank <= 1_000:
        return "top-1k"
    if rank <= 10_000:
        return "top-10k"
    if rank <= 100_000:
        return "top-100k"
    if rank <= 1_000_000:
        return "top-1m"
    return "long-tail"


async def run(min_score: int, force: bool) -> int:
    cfg = Config.from_env()
    tranco = load_tranco(cfg.tranco_csv_path)
    print(f"[enrich] loaded {len(tranco)} Tranco entries", file=sys.stderr)
    seen = set() if force else read_existing_keys(ENRICHED_PATH, "domain")
    written = 0
    for rec in iter_records(DETECTIONS_PATH):
        d = rec.get("domain")
        if not isinstance(d, str) or d in seen:
            continue
        if (rec.get("score") or 0) < min_score:
            continue
        out = dict(rec)
        out["tranco_rank"] = tranco.get(d)
        if cfg.cf_radar_token:
            try:
                out["cf_radar_bucket"] = await cf_radar_bucket(d, cfg.cf_radar_token, cfg)
            except Exception as exc:
                print(f"[enrich] cf-radar error for {d}: {exc}", file=sys.stderr)
                out["cf_radar_bucket"] = None
        else:
            out["cf_radar_bucket"] = None
        out["enriched_at"] = datetime.now(timezone.utc).isoformat()
        append_record(ENRICHED_PATH, out)
        written += 1
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--min-score", type=int, default=70)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    n = asyncio.run(run(args.min_score, args.force))
    print(f"[enrich] wrote {n} records to {ENRICHED_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
