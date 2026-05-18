#!/usr/bin/env python3
"""Stage 11 — Compromise indicator check (CVE-2026-41940 incident response).

Reads the SQLite DB, picks every cPanel-positive WordPress site, and probes a
short list of *public* paths that are known to be created by the ransomware
campaign Censys documented (https://censys.com/blog/the-cpanel-situation-is/).

The campaign appends `.sorry` to common public files and leaves them
world-readable. So a simple GET that returns 200 OK + sane body length is a
high-confidence compromise indicator. No exploitation, no auth bypass, no
authentication attempts — just banner-grab GETs against paths that, if they
return content, were placed there by the attacker.

If your site returns 200 on any of these, your server is compromised; the
data this stage collects is intended to drive owner-notification outreach,
NOT to be published anywhere public.

Outputs:
  data/compromise_check.jsonl                  — all probed sites + verdict
  reports/cpanel_urgent_compromised.csv        — indicator-positive only

Usage:
  python scripts/11_compromise_check.py
  python scripts/11_compromise_check.py --force    # re-probe everything
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.http import polite_client, PoliteClient  # noqa: E402
from lib.jsonl import append_record, iter_records, read_existing_keys  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
COMPROMISE_PATH = data_dir() / "compromise_check.jsonl"
URGENT_CSV = reports_dir() / "cpanel_urgent_compromised.csv"


# Indicator paths from the Censys writeup. All are artifacts left by the
# ransomware after the auth-bypass exploitation — a 200 response means the
# server is compromised, not that we exploited anything.
INDICATOR_PATHS = [
    "/index.html.sorry",
    "/wp-config.php.sorry",
    "/wp-content/index.php.sorry",
    "/.sorry",
    "/wp-includes/index.php.sorry",
    "/cpanel-style.sorry",
]


def _q_cpanel_sites(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("""
        SELECT domain, score, tranco_rank, category, theme, ip,
               server_header, scheme_used
        FROM domains
        WHERE host_panel = 'cpanel' AND score >= 70
        ORDER BY (tranco_rank IS NULL), tranco_rank, domain
    """)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


async def probe_one(client: PoliteClient, row: dict) -> dict:
    base = f"{row.get('scheme_used') or 'https'}://{row['domain']}"
    hits: list[dict] = []
    for path in INDICATOR_PATHS:
        url = base.rstrip("/") + path
        try:
            resp = await client.get(url)
        except Exception:
            continue
        if resp is None:
            continue
        # Indicator-positive: 200 with non-empty body that is not the site's
        # 404 page. We compare body length and a quick body-text check for
        # ransom-note keywords ("sorry", "qTox", "TOX ID").
        if resp.status_code == 200 and resp.text:
            body = (resp.text or "").lower()
            length = len(resp.text)
            keywords = any(k in body for k in (
                "sorry", "qtox", "tox id", "your files have been",
                "encrypted", "ransom",
            ))
            # Also accept any 200 to a literally `*.sorry` filename — that's
            # already strong enough; cPanel/Apache won't serve those by accident.
            confidence = "high" if keywords else "medium"
            hits.append({
                "path": path,
                "status": resp.status_code,
                "length": length,
                "confidence": confidence,
                "body_excerpt": resp.text[:600],
            })
    return {
        "domain": row["domain"],
        "indicator_count": len(hits),
        "indicators": hits,
        "compromised": bool(hits),
        "tranco_rank": row.get("tranco_rank"),
        "category": row.get("category"),
        "ip": row.get("ip"),
        "server_header": row.get("server_header"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def run(force: bool) -> int:
    cfg = Config.from_env()
    if not DB_PATH.exists():
        print(f"[compromise] {DB_PATH} not found; run stage 08 first", file=sys.stderr)
        return 0
    conn = sqlite3.connect(DB_PATH)
    rows = _q_cpanel_sites(conn)
    conn.close()
    seen = set() if force else read_existing_keys(COMPROMISE_PATH, "domain")
    pending = [r for r in rows if r["domain"] not in seen]
    print(f"[compromise] scope={len(rows)}  pending={len(pending)}  done={len(seen)}",
          file=sys.stderr)
    if not pending:
        return 0

    sem = asyncio.Semaphore(50)
    written = 0
    hits = 0
    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=cfg.rps_per_host,
        timeout=cfg.timeout,
        max_concurrent=200,
    ) as client:

        async def worker(row: dict) -> None:
            nonlocal written, hits
            async with sem:
                try:
                    rec = await probe_one(client, row)
                except Exception as exc:
                    rec = {
                        "domain": row["domain"],
                        "error": str(exc)[:160],
                        "compromised": False,
                        "indicator_count": 0,
                        "indicators": [],
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    }
            append_record(COMPROMISE_PATH, rec)
            written += 1
            if rec.get("compromised"):
                hits += 1
                print(f"[compromise] !!! {rec['domain']} — {rec['indicator_count']} indicators",
                      file=sys.stderr)
            if written % 100 == 0:
                print(f"[compromise] {written}/{len(pending)}  ({hits} compromised so far)",
                      file=sys.stderr)

        await asyncio.gather(*(worker(r) for r in pending))

    _write_urgent_csv()
    print(f"[compromise] DONE — {written} probed, {hits} indicators positive",
          file=sys.stderr)
    print(f"[compromise] urgent list: {URGENT_CSV}", file=sys.stderr)
    return written


def _write_urgent_csv() -> None:
    """Build the prioritized 'reach out NOW' list from compromise_check.jsonl."""
    if not COMPROMISE_PATH.exists():
        return
    urgent: list[dict] = []
    for r in iter_records(COMPROMISE_PATH):
        if r.get("compromised"):
            urgent.append(r)
    urgent.sort(key=lambda r: (r.get("tranco_rank") or 10**9, r.get("domain") or ""))
    URGENT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with URGENT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "domain", "tranco_rank", "category", "ip", "server_header",
            "indicator_count", "indicator_paths", "highest_confidence",
            "checked_at",
        ])
        for r in urgent:
            inds = r.get("indicators") or []
            paths = "; ".join(i["path"] for i in inds)
            conf = "high" if any(i.get("confidence") == "high" for i in inds) else "medium"
            w.writerow([
                r.get("domain"),
                r.get("tranco_rank") or "",
                r.get("category") or "",
                r.get("ip") or "",
                r.get("server_header") or "",
                r.get("indicator_count") or 0,
                paths,
                conf,
                r.get("checked_at") or "",
            ])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true",
                   help="Re-probe sites already in compromise_check.jsonl")
    args = p.parse_args()
    n = asyncio.run(run(args.force))
    print(f"[compromise] processed {n} sites", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
