#!/usr/bin/env python3
"""Stage 23 — Freshness (last-post date from /feed/).

For every cPanel WordPress site, fetch /feed/, parse the newest `<pubDate>`
and count how many posts landed in the last 90 days. The signal answers
"is this site alive?" — a dead site is unsellable.

Writes to the `freshness` table. Idempotent.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.contacts import open_conn  # noqa: E402
from lib.http import polite_client, PoliteClient  # noqa: E402
from lib.jsonl import append_record, read_existing_keys  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
FRESHNESS_PATH = data_dir() / "freshness.jsonl"

SCOPES = {
    "cpanel-wp": "host_panel='cpanel' AND score>=70",
    "wp-positive": "score>=70",
    "all": "1=1",
}

NS_DC = "{http://purl.org/dc/elements/1.1/}"
NS_ATOM = "{http://www.w3.org/2005/Atom}"

# Atom + ISO-8601 fallback regex (the strict parser is for RSS RFC 822)
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def parse_dt(text: str) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    # RSS 2.0 format
    try:
        dt = parsedate_to_datetime(text)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    # ISO-8601 (Atom)
    iso = _ISO_RE.search(text)
    if iso:
        try:
            t = iso.group(0).replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def extract_post_dates(xml_text: str) -> list[datetime]:
    dts: list[datetime] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return dts
    # RSS 2.0 — channel/item/pubDate
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag in ("pubdate", "date", "updated", "published"):
            d = parse_dt(el.text or "")
            if d:
                dts.append(d)
    return dts


async def fetch_freshness(client: PoliteClient, domain: str, scheme: str) -> dict:
    feed_urls = [
        f"{scheme}://{domain}/feed/",
        f"{scheme}://{domain}/?feed=rss2",
        f"{scheme}://{domain}/atom.xml",
        f"{scheme}://{domain}/feed.xml",
    ]
    chosen_url = None
    dates: list[datetime] = []
    for url in feed_urls:
        try:
            resp = await client.get(url)
        except Exception:
            continue
        if resp is None or resp.status_code != 200 or not resp.text:
            continue
        dts = extract_post_dates(resp.text)
        if dts:
            chosen_url = url
            dates = dts
            break
    if not dates:
        return {
            "domain": domain,
            "last_post_at": None,
            "posts_last_90d": 0,
            "feed_url": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    last_post = max(dates)
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    recent = sum(1 for d in dates if d >= cutoff)
    return {
        "domain": domain,
        "last_post_at": last_post.isoformat(),
        "posts_last_90d": recent,
        "feed_url": chosen_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def write_to_schema(conn, rec: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO freshness
            (domain, last_post_at, posts_last_90d, feed_url, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (rec["domain"], rec.get("last_post_at"), rec.get("posts_last_90d"),
         rec.get("feed_url"), rec.get("fetched_at")),
    )


async def run(scope: str, force: bool, concurrency: int, limit: int | None) -> int:
    cfg = Config.from_env()
    conn = open_conn(DB_PATH)
    where = SCOPES.get(scope, SCOPES["cpanel-wp"])
    rows = conn.execute(
        f"SELECT domain, scheme_used FROM domains WHERE {where} "
        "ORDER BY (tranco_rank IS NULL), tranco_rank"
    ).fetchall()
    if limit:
        rows = rows[:limit]
    seen = set() if force else read_existing_keys(FRESHNESS_PATH, "domain")
    pending = [(r["domain"], r["scheme_used"] or "https") for r in rows if r["domain"] not in seen]
    print(f"[freshness] scope={scope}  total={len(rows)}  pending={len(pending)}",
          file=sys.stderr)
    if not pending:
        return 0
    sem = asyncio.Semaphore(concurrency)
    written = 0
    with_dates = 0
    active_90d = 0
    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=cfg.rps_per_host,
        timeout=cfg.timeout,
        max_concurrent=150,
    ) as client:

        async def worker(domain: str, scheme: str) -> None:
            nonlocal written, with_dates, active_90d
            async with sem:
                try:
                    rec = await fetch_freshness(client, domain, scheme)
                except Exception as exc:
                    rec = {
                        "domain": domain, "error": str(exc)[:160],
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
            append_record(FRESHNESS_PATH, rec)
            if "error" not in rec:
                write_to_schema(conn, rec)
                if rec.get("last_post_at"):
                    with_dates += 1
                if rec.get("posts_last_90d", 0) > 0:
                    active_90d += 1
            written += 1
            if written % 100 == 0:
                conn.commit()
                print(f"[freshness] {written}/{len(pending)} "
                      f"with_post_date={with_dates} active_90d={active_90d}",
                      file=sys.stderr)

        await asyncio.gather(*(worker(d, s) for d, s in pending))
    conn.commit()
    conn.close()
    print(f"[freshness] FINAL with_dates={with_dates}  active_90d={active_90d}",
          file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=list(SCOPES.keys()), default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--concurrency", type=int, default=80)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    n = asyncio.run(run(args.scope, args.force, args.concurrency, args.limit))
    print(f"[freshness] wrote {n} records to {FRESHNESS_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
