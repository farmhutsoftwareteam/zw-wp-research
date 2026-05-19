#!/usr/bin/env python3
"""Stage 27 — Google PageSpeed Insights.

For each cPanel WP site, query the PSI API for mobile + desktop strategies.
Captures Performance / Accessibility / Best-Practices / SEO scores plus
core web vitals (LCP, CLS, TBT) so we can pitch concrete improvements.

Gated on `GOOGLE_PSI_API_KEY` in `.env`. Free tier: 25,000 queries/day,
1 query/sec rate limit. Get a key at https://developers.google.com/speed/docs/insights/v5/get-started

Writes to `pagespeed` table. Idempotent.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.contacts import open_conn  # noqa: E402
from lib.http import polite_client, PoliteClient  # noqa: E402
from lib.jsonl import append_record, read_existing_keys  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
PSI_PATH = data_dir() / "pagespeed.jsonl"
PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

SCOPES = {
    "cpanel-wp": "host_panel='cpanel' AND score>=70",
    "wp-positive": "score>=70",
    "all": "1=1",
}

STRATEGIES = ("mobile", "desktop")


def _score_pct(lighthouse_result: dict, category: str) -> float | None:
    try:
        cat = (lighthouse_result.get("categories") or {}).get(category) or {}
        s = cat.get("score")
        return round(float(s) * 100, 1) if s is not None else None
    except (TypeError, ValueError):
        return None


def _audit_numeric(lighthouse_result: dict, key: str) -> float | None:
    audits = lighthouse_result.get("audits") or {}
    a = audits.get(key) or {}
    v = a.get("numericValue")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def fetch_psi(client: PoliteClient, url: str, strategy: str, api_key: str) -> dict | None:
    params = {
        "url": url,
        "strategy": strategy,
        "category": ["performance", "accessibility", "best-practices", "seo"],
        "key": api_key,
    }
    try:
        # httpx wants repeated `category=` params, list works
        resp = await client.client.get(PSI_URL, params=params, timeout=90)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    lh = data.get("lighthouseResult") or {}
    return {
        "performance": _score_pct(lh, "performance"),
        "accessibility": _score_pct(lh, "accessibility"),
        "best_practices": _score_pct(lh, "best-practices"),
        "seo": _score_pct(lh, "seo"),
        "lcp_ms": int(_audit_numeric(lh, "largest-contentful-paint") or 0) or None,
        "cls": _audit_numeric(lh, "cumulative-layout-shift"),
        "tbt_ms": int(_audit_numeric(lh, "total-blocking-time") or 0) or None,
    }


def write_to_schema(conn, domain: str, strategy: str, rec: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO pagespeed
            (domain, strategy, performance, accessibility, best_practices, seo,
             lcp_ms, cls, tbt_ms, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (domain, strategy, rec.get("performance"), rec.get("accessibility"),
         rec.get("best_practices"), rec.get("seo"),
         rec.get("lcp_ms"), rec.get("cls"), rec.get("tbt_ms")),
    )


async def run(scope: str, force: bool, top_n: int | None,
              strategies: list[str]) -> int:
    api_key = os.environ.get("GOOGLE_PSI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("[psi] no GOOGLE_PSI_API_KEY in .env — no-op",
              file=sys.stderr)
        print("[psi] sign up at https://developers.google.com/speed/docs/insights/v5/get-started",
              file=sys.stderr)
        return 0
    cfg = Config.from_env()
    conn = open_conn(DB_PATH)
    where = SCOPES.get(scope, SCOPES["cpanel-wp"])
    rows = conn.execute(
        f"SELECT domain, scheme_used FROM domains WHERE {where} "
        "ORDER BY (tranco_rank IS NULL), tranco_rank"
    ).fetchall()
    if top_n:
        rows = rows[:top_n]
    seen = set() if force else read_existing_keys(PSI_PATH, "domain")
    pending = [(r["domain"], r["scheme_used"] or "https") for r in rows if r["domain"] not in seen]
    print(f"[psi] scope={scope}  top_n={top_n}  pending={len(pending)}  "
          f"strategies={strategies}", file=sys.stderr)
    if not pending:
        return 0
    written = 0
    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=1.0,  # Google PSI 1 qps free tier
        timeout=120,
        max_concurrent=4,
    ) as client:
        for domain, scheme in pending:
            rec = {
                "domain": domain,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "strategies": {},
            }
            full_url = f"{scheme}://{domain}/"
            for strategy in strategies:
                result = await fetch_psi(client, full_url, strategy, api_key)
                if result:
                    rec["strategies"][strategy] = result
                    write_to_schema(conn, domain, strategy, result)
            append_record(PSI_PATH, rec)
            written += 1
            if written % 10 == 0:
                conn.commit()
                print(f"[psi] {written}/{len(pending)}", file=sys.stderr)
    conn.commit()
    conn.close()
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=list(SCOPES.keys()), default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--top-n", type=int, default=200,
                   help="cap to top N by Tranco rank (PSI is slow)")
    p.add_argument("--strategy", choices=list(STRATEGIES) + ["both"],
                   default="mobile")
    args = p.parse_args()
    strategies = list(STRATEGIES) if args.strategy == "both" else [args.strategy]
    n = asyncio.run(run(args.scope, args.force, args.top_n, strategies))
    print(f"[psi] wrote {n} records to {PSI_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
