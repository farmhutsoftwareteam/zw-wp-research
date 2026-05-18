#!/usr/bin/env python3
"""Stage 18 — Inner-page deep scrape.

Fans out from sitemap.xml discovery to /privacy, /tos, /about, /team and
similar contact-relevant pages, then runs the page-contact extractor.
Captures names paired with emails/phones via the schema.org Person and
heuristic header-near-mailto patterns.

Writes through to contacts/channels. Idempotent.
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
from lib.http import polite_client, PoliteClient  # noqa: E402
from lib.jsonl import append_record, read_existing_keys  # noqa: E402
from lib.page_contact import extract_contact  # noqa: E402
from lib.sitemap import discover_contact_urls  # noqa: E402

try:
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment]

DB_PATH = reports_dir() / "zwwp.db"
DEEP_PATH = data_dir() / "deep_scrape.jsonl"

SCOPES = {
    "cpanel-wp": "host_panel='cpanel' AND score>=70",
    "wp-positive": "score>=70",
    "all": "1=1",
}


async def scrape_one(client: PoliteClient, domain: str, scheme: str) -> dict:
    base = f"{scheme}://{domain}"
    pages_visited: list[str] = []
    aggregate: dict = {"emails": [], "phones": [], "addresses": [],
                       "socials": [], "persons": []}
    urls = await discover_contact_urls(client, base, max_urls=10)
    # Fallback: hit /about and /contact directly if sitemap returned nothing
    if not urls:
        for path in ("/about", "/about-us", "/contact", "/contact-us",
                     "/team", "/staff", "/privacy", "/privacy-policy"):
            urls.append(base + path)
    for url in urls[:10]:
        try:
            resp = await client.get(url)
        except Exception:
            continue
        if resp is None or resp.status_code != 200 or not resp.text:
            continue
        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")
        page_data = extract_contact(soup, source_url=url)
        pages_visited.append(url)
        for k in ("emails", "phones", "addresses"):
            for v in page_data.get(k, []) or []:
                if v not in aggregate[k]:
                    aggregate[k].append(v)
        for s in page_data.get("socials", []) or []:
            if s not in aggregate["socials"]:
                aggregate["socials"].append(s)
        for p in page_data.get("persons", []) or []:
            if p not in aggregate["persons"]:
                aggregate["persons"].append(p)
    return {
        "domain": domain,
        "pages_visited": pages_visited,
        **aggregate,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def write_to_schema(conn, rec: dict) -> dict[str, int]:
    counts = {"persons": 0, "emails": 0, "phones": 0, "addresses": 0, "socials": 0}
    domain = rec["domain"]
    # Persons get a contact each with their email/phone.
    for p in rec.get("persons") or []:
        name = p.get("name")
        cid = upsert_contact(
            conn, domain=domain, source="homepage_deep",
            confidence=0.65, display_name=name, role="person_on_about",
        )
        counts["persons"] += 1
        if p.get("email"):
            if add_channel(
                conn, contact_id=cid, kind="email", value=p["email"].lower(),
                source="homepage_deep", confidence=0.7,
            ) is not None:
                counts["emails"] += 1
        if p.get("phone"):
            if add_channel(
                conn, contact_id=cid, kind="phone", value=p["phone"],
                source="homepage_deep", confidence=0.6,
            ) is not None:
                counts["phones"] += 1
    # Unattached emails/phones/addresses → a generic "site contact"
    has_generic = bool(rec.get("emails") or rec.get("phones")
                       or rec.get("addresses") or rec.get("socials"))
    if has_generic:
        gid = upsert_contact(
            conn, domain=domain, source="homepage_deep",
            confidence=0.55, role="site_contact",
        )
        for email in rec.get("emails") or []:
            if "@" in email and add_channel(
                conn, contact_id=gid, kind="email", value=email.lower(),
                source="homepage_deep", confidence=0.6,
            ) is not None:
                counts["emails"] += 1
        for phone in rec.get("phones") or []:
            if add_channel(
                conn, contact_id=gid, kind="phone", value=phone,
                source="homepage_deep", confidence=0.5,
            ) is not None:
                counts["phones"] += 1
        for addr in rec.get("addresses") or []:
            if add_channel(
                conn, contact_id=gid, kind="address", value=addr,
                source="homepage_deep", confidence=0.55,
            ) is not None:
                counts["addresses"] += 1
        for s in rec.get("socials") or []:
            if isinstance(s, dict) and add_channel(
                conn, contact_id=gid, kind=s["kind"], value=s["url"],
                source="homepage_deep", confidence=0.5,
            ) is not None:
                counts["socials"] += 1
    return counts


async def run(scope: str, force: bool, concurrency: int, limit: int | None) -> int:
    cfg = Config.from_env()
    conn = open_conn(DB_PATH)
    where = SCOPES.get(scope, SCOPES["cpanel-wp"])
    # Focus on domains that don't already have a >=0.6-confidence email contact
    rows = conn.execute(f"""
        SELECT d.domain, d.scheme_used
        FROM domains d
        LEFT JOIN (
            SELECT c.domain
            FROM contacts c
            JOIN channels ch ON ch.contact_id=c.id
            WHERE ch.kind='email' AND ch.confidence>=0.6
            GROUP BY c.domain
        ) g ON g.domain=d.domain
        WHERE {where}
        ORDER BY (d.tranco_rank IS NULL), d.tranco_rank
    """).fetchall()
    if limit:
        rows = rows[:limit]
    seen = set() if force else read_existing_keys(DEEP_PATH, "domain")
    pending = [(r["domain"], r["scheme_used"] or "https") for r in rows if r["domain"] not in seen]
    print(f"[deep] scope={scope}  total={len(rows)}  pending={len(pending)}",
          file=sys.stderr)
    if not pending:
        return 0
    totals = {"persons": 0, "emails": 0, "phones": 0, "addresses": 0, "socials": 0}
    sem = asyncio.Semaphore(concurrency)
    written = 0
    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=cfg.rps_per_host,
        timeout=cfg.timeout,
        max_concurrent=100,
    ) as client:

        async def worker(domain: str, scheme: str) -> None:
            nonlocal written
            async with sem:
                try:
                    rec = await scrape_one(client, domain, scheme)
                except Exception as exc:
                    rec = {
                        "domain": domain, "error": str(exc)[:200],
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                append_record(DEEP_PATH, rec)
                if "error" not in rec:
                    c = write_to_schema(conn, rec)
                    for k in totals:
                        totals[k] += c.get(k, 0)
            written += 1
            if written % 50 == 0:
                conn.commit()
                print(f"[deep] {written}/{len(pending)} totals={totals}",
                      file=sys.stderr)

        await asyncio.gather(*(worker(d, s) for d, s in pending))
    conn.commit()
    conn.close()
    print(f"[deep] final totals: {totals}", file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=list(SCOPES.keys()), default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--concurrency", type=int, default=30)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    n = asyncio.run(run(args.scope, args.force, args.concurrency, args.limit))
    print(f"[deep] wrote {n} records to {DEEP_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
