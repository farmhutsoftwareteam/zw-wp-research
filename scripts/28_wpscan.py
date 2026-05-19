#!/usr/bin/env python3
"""Stage 28 — WPScan plugin / theme / core vulnerability lookup.

For each plugin we detected on a cPanel WP site (in the `plugins` table),
query the WPScan API for known CVEs. Stores results in `vulnerabilities`.

Gated on `WPSCAN_API_TOKEN` in `.env`. Free tier: 25 requests/day, sign up
at https://wpscan.com/profile/

Strategy to stretch the daily quota:
  1. Get distinct (plugin_slug, version) pairs across the dataset
  2. Query each one ONCE; cache to data/wpscan_cache.jsonl
  3. Cross-reference into the per-domain vulnerabilities table
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.contacts import open_conn  # noqa: E402
from lib.http import polite_client, PoliteClient  # noqa: E402
from lib.jsonl import append_record  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
CACHE_PATH = data_dir() / "wpscan_cache.jsonl"


async def fetch_plugin(client: PoliteClient, slug: str, token: str) -> dict | None:
    url = f"https://wpscan.com/api/v3/plugins/{slug}"
    try:
        resp = await client.client.get(
            url,
            headers={"Authorization": f"Token token={token}",
                     "User-Agent": "zw-wp-research/0.1"},
            timeout=30,
        )
    except Exception:
        return None
    if resp.status_code == 404:
        return {"slug": slug, "vulnerabilities": []}
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def _load_cache() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not CACHE_PATH.exists():
        return out
    with CACHE_PATH.open("rb") as f:
        for line in f:
            try:
                rec = json.loads(line)
                if rec.get("slug"):
                    out[rec["slug"]] = rec
            except Exception:
                continue
    return out


def write_to_schema(conn, domain: str, plugin: str, raw: dict) -> int:
    """Insert per-CVE rows into `vulnerabilities`. Returns count of new rows."""
    n = 0
    if not raw:
        return 0
    # The WPScan API nests vulns under plugin slug → vulnerabilities[].
    vulns = []
    if isinstance(raw, dict):
        plugin_data = raw.get(plugin) or raw
        vulns = plugin_data.get("vulnerabilities") or []
    for v in vulns:
        cve = None
        for ref in v.get("references", {}).get("cve", []) or []:
            cve = "CVE-" + ref if not str(ref).startswith("CVE-") else ref
            break
        title = v.get("title")
        severity = (v.get("cvss") or {}).get("severity") if isinstance(v.get("cvss"), dict) else None
        fixed_in = v.get("fixed_in")
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO vulnerabilities
                    (domain, component_type, component, version, cve, title,
                     severity, fixed_in, published_at, fetched_at)
                VALUES (?, 'plugin', ?, NULL, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (domain, plugin, cve or f"WPSCAN-{plugin}-{title[:30] if title else 'unk'}",
                 title, severity, fixed_in, v.get("published_at")),
            )
            n += 1
        except Exception:
            continue
    return n


async def run(scope: str, force: bool, daily_cap: int) -> int:
    token = os.environ.get("WPSCAN_API_TOKEN")
    if not token:
        print("[wpscan] no WPSCAN_API_TOKEN in .env — no-op",
              file=sys.stderr)
        print("[wpscan] sign up at https://wpscan.com/profile/ (free, 25/day)",
              file=sys.stderr)
        return 0
    cfg = Config.from_env()
    conn = open_conn(DB_PATH)
    # Distinct plugins across the cPanel-WP scope
    rows = conn.execute("""
        SELECT DISTINCT p.plugin
        FROM plugins p
        JOIN domains d ON d.domain = p.domain
        WHERE d.host_panel='cpanel' AND d.score>=70
        ORDER BY p.plugin
    """).fetchall()
    plugins = [r["plugin"] for r in rows]
    cache = {} if force else _load_cache()
    pending = [p for p in plugins if p not in cache]
    if daily_cap:
        pending = pending[:daily_cap]
    print(f"[wpscan] plugins distinct={len(plugins)} cached={len(cache)} "
          f"pending={len(pending)}", file=sys.stderr)

    written = 0
    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=0.5,
        timeout=30,
        max_concurrent=3,
    ) as client:
        for slug in pending:
            data = await fetch_plugin(client, slug, token)
            rec = {"slug": slug, "fetched_at": datetime.now(timezone.utc).isoformat(),
                   "raw": data}
            append_record(CACHE_PATH, rec)
            cache[slug] = rec
            written += 1
            if written % 5 == 0:
                print(f"[wpscan] {written} plugins fetched", file=sys.stderr)

    # Cross-reference: for every (domain, plugin) pair, attach known CVEs
    pairs = conn.execute("""
        SELECT p.domain, p.plugin
        FROM plugins p
        JOIN domains d ON d.domain = p.domain
        WHERE d.host_panel='cpanel' AND d.score>=70
    """).fetchall()
    inserted = 0
    for r in pairs:
        slug = r["plugin"]
        cached = cache.get(slug)
        if not cached or not cached.get("raw"):
            continue
        inserted += write_to_schema(conn, r["domain"], slug, cached["raw"])
    conn.commit()
    conn.close()
    print(f"[wpscan] FINAL plugins_fetched={written}  vuln_rows={inserted}",
          file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=["cpanel-wp"], default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--daily-cap", type=int, default=25,
                   help="max API calls (free tier is 25/day)")
    args = p.parse_args()
    n = asyncio.run(run(args.scope, args.force, args.daily_cap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
