#!/usr/bin/env python3
"""Stage 20 — Free email-finder API rotation.

Final gap-filler: for each cPanel WP domain with no high-confidence email,
query the rotator (Hunter / Apollo / Snov / Tomba) for verified emails.

Skeleton-safe: no-ops cleanly when no API keys are configured. Reads keys
from .env via lib/config + env. Drop these into your .env to activate:

  HUNTER_API_KEY=...
  APOLLO_API_KEY=...
  SNOV_CLIENT_ID=...
  SNOV_CLIENT_SECRET=...
  TOMBA_API_KEY=...
  TOMBA_API_SECRET=...
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.contacts import add_channel, open_conn, upsert_contact  # noqa: E402
from lib.email_finder import FinderRotator  # noqa: E402
from lib.http import polite_client  # noqa: E402
from lib.jsonl import append_record, read_existing_keys  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
FINDER_PATH = data_dir() / "finder.jsonl"

SCOPES = {
    "cpanel-wp": "host_panel='cpanel' AND score>=70",
    "wp-positive": "score>=70",
    "all": "1=1",
}


def write_to_schema(conn, domain: str, results: list[dict]) -> int:
    n = 0
    for r in results:
        email = (r.get("email") or "").strip().lower()
        if not email or "@" not in email:
            continue
        display = " ".join(filter(None, [r.get("first_name"), r.get("last_name")])) or None
        cid = upsert_contact(
            conn, domain=domain, source=r.get("source_provider", "finder"),
            confidence=min(0.8, max(0.3, float(r.get("confidence") or 0.5))),
            display_name=display, role="finder_api",
        )
        if add_channel(
            conn, contact_id=cid, kind="email", value=email,
            source=r.get("source_provider", "finder"),
            confidence=min(0.8, max(0.3, float(r.get("confidence") or 0.5))),
            verified=bool(r.get("verified")),
        ) is not None:
            n += 1
    return n


async def run(scope: str, force: bool, limit: int | None) -> int:
    cfg = Config.from_env()
    rotator = FinderRotator()
    configured = rotator.configured()
    if not configured:
        print("[finder] no providers configured (HUNTER_API_KEY etc. not set in .env)",
              file=sys.stderr)
        print("[finder] sign up for free tiers and add keys; this stage no-ops until then",
              file=sys.stderr)
        return 0
    print(f"[finder] configured providers: {[p.name for p in configured]}",
          file=sys.stderr)
    conn = open_conn(DB_PATH)
    where = SCOPES.get(scope, SCOPES["cpanel-wp"])
    rows = conn.execute(f"""
        SELECT d.domain FROM domains d
        LEFT JOIN (
            SELECT c.domain
            FROM contacts c JOIN channels ch ON ch.contact_id=c.id
            WHERE ch.kind='email' AND ch.confidence>=0.6
            GROUP BY c.domain
        ) g ON g.domain=d.domain
        WHERE {where} AND g.domain IS NULL
        ORDER BY (d.tranco_rank IS NULL), d.tranco_rank
    """).fetchall()
    domains = [r["domain"] for r in rows]
    if limit:
        domains = domains[:limit]
    seen = set() if force else read_existing_keys(FINDER_PATH, "domain")
    pending = [d for d in domains if d not in seen]
    print(f"[finder] gap domains: {len(domains)}  pending: {len(pending)}",
          file=sys.stderr)
    if not pending:
        return 0
    written = 0
    total_emails = 0
    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=0.5,
        timeout=30,
        max_concurrent=4,
    ) as client:
        for domain in pending:
            try:
                results = await rotator.find(domain, client)
            except Exception as exc:
                results = []
                print(f"[finder] {domain} error: {exc}", file=sys.stderr)
            rec = {
                "domain": domain,
                "results": results,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            append_record(FINDER_PATH, rec)
            if results:
                total_emails += write_to_schema(conn, domain, results)
                conn.commit()
            written += 1
            if written % 10 == 0:
                print(f"[finder] {written}/{len(pending)} emails_added={total_emails}",
                      file=sys.stderr)
    conn.close()
    print(f"[finder] done. emails added: {total_emails}", file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=list(SCOPES.keys()), default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    n = asyncio.run(run(args.scope, args.force, args.limit))
    print(f"[finder] wrote {n} records to {FINDER_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
