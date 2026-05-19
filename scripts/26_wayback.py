#!/usr/bin/env python3
"""Stage 26 — Wayback first-snapshot (domain age proxy).

For each cPanel WP site, query the Internet Archive CDX API for the first
captured snapshot of the domain. The age proxy is useful for:
  - Filtering brand-new sites (likely abandoned hobby projects)
  - Identifying long-established businesses (>5 years = high inertia, easier
    to upsell hosting/maintenance to)

Writes to `domain_age` table. Idempotent.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.contacts import open_conn  # noqa: E402
from lib.http import polite_client, PoliteClient  # noqa: E402
from lib.jsonl import append_record, read_existing_keys  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
WAYBACK_PATH = data_dir() / "wayback.jsonl"

SCOPES = {
    "cpanel-wp": "host_panel='cpanel' AND score>=70",
    "wp-positive": "score>=70",
    "all": "1=1",
}


async def fetch_wayback(client: PoliteClient, domain: str) -> dict:
    """Use the CDX API to find the first capture + total capture count."""
    cdx_url = (
        "https://web.archive.org/cdx/search/cdx?"
        f"url={domain}/&output=json&limit=1&fl=timestamp&filter=statuscode:200"
    )
    first_ts: str | None = None
    try:
        resp = await client.get(cdx_url)
        if resp is not None and resp.status_code == 200 and resp.text:
            data = json.loads(resp.text)
            if isinstance(data, list) and len(data) > 1:
                first_ts = data[1][0]
    except Exception:
        first_ts = None
    count_url = (
        f"https://web.archive.org/cdx/search/cdx?url={domain}/&output=json&showNumPages=true"
    )
    snapshot_count: int = 0
    try:
        resp = await client.get(count_url)
        if resp is not None and resp.status_code == 200 and resp.text.strip():
            try:
                pages = int(resp.text.strip())
                # Each page is ~150 records; conservative estimate
                snapshot_count = pages * 150
            except ValueError:
                snapshot_count = 0
    except Exception:
        snapshot_count = 0
    first_iso = None
    if first_ts and len(first_ts) >= 8:
        try:
            first_iso = datetime.strptime(first_ts[:14], "%Y%m%d%H%M%S").replace(
                tzinfo=timezone.utc
            ).isoformat()
        except ValueError:
            try:
                first_iso = datetime.strptime(first_ts[:8], "%Y%m%d").replace(
                    tzinfo=timezone.utc
                ).isoformat()
            except ValueError:
                first_iso = None
    return {
        "domain": domain,
        "first_archived_at": first_iso,
        "snapshots_count": snapshot_count,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def write_to_schema(conn, rec: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO domain_age
            (domain, first_archived_at, snapshots_count, fetched_at)
        VALUES (?, ?, ?, ?)
        """,
        (rec["domain"], rec.get("first_archived_at"),
         rec.get("snapshots_count"), rec.get("fetched_at")),
    )


async def run(scope: str, force: bool, concurrency: int, limit: int | None) -> int:
    cfg = Config.from_env()
    conn = open_conn(DB_PATH)
    where = SCOPES.get(scope, SCOPES["cpanel-wp"])
    rows = conn.execute(
        f"SELECT domain FROM domains WHERE {where} "
        "ORDER BY (tranco_rank IS NULL), tranco_rank"
    ).fetchall()
    domains = [r["domain"] for r in rows]
    if limit:
        domains = domains[:limit]
    seen = set() if force else read_existing_keys(WAYBACK_PATH, "domain")
    pending = [d for d in domains if d not in seen]
    print(f"[wayback] scope={scope}  total={len(domains)}  pending={len(pending)}",
          file=sys.stderr)
    if not pending:
        return 0
    sem = asyncio.Semaphore(concurrency)
    written = 0
    archived = 0

    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=2.0,  # archive.org is OK with moderate fan-out
        timeout=20,
        max_concurrent=30,
    ) as client:

        async def worker(domain: str) -> None:
            nonlocal written, archived
            async with sem:
                try:
                    rec = await fetch_wayback(client, domain)
                except Exception as exc:
                    rec = {
                        "domain": domain, "error": str(exc)[:160],
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
            append_record(WAYBACK_PATH, rec)
            if rec.get("first_archived_at"):
                archived += 1
                write_to_schema(conn, rec)
            written += 1
            if written % 25 == 0:
                conn.commit()
                print(f"[wayback] {written}/{len(pending)} archived={archived}",
                      file=sys.stderr)

        await asyncio.gather(*(worker(d) for d in pending))
    conn.commit()
    conn.close()
    print(f"[wayback] FINAL archived={archived}/{written}", file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=list(SCOPES.keys()), default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--concurrency", type=int, default=15)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    n = asyncio.run(run(args.scope, args.force, args.concurrency, args.limit))
    print(f"[wayback] wrote {n} records to {WAYBACK_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
