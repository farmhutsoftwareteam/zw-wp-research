#!/usr/bin/env python3
"""Stage 17 — WHOIS / RDAP enrichment.

For each cPanel-positive WP domain, query free WHOIS providers (RDAP → Whoxy
→ Who-Dat). Extract registrant / admin / tech emails + registrant name +
country. Privacy-proxy emails get confidence 0.2; real-looking ones 0.6.

Writes through to contacts/channels. Idempotent.

================================================================
*** KNOWN LIMITATION: .zw TLD HAS NO PUBLIC WHOIS DATA ***

Verified 2026-05: the Zimbabwean registry (POTRAZ / TelOne) does NOT
operate a publicly-queryable WHOIS server. IANA's WHOIS for `.zw`
returns POTRAZ + TelOne contact info but no `refer:` field — meaning
no per-domain WHOIS is available from any provider:

  - rdap.org → 404 for `.co.zw`
  - api.whoxy.com → "Unsupported Domain Extension: co.zw"
  - who-dat.as93.net → Vercel security checkpoint (anti-bot)
  - whois.nic.zw / whois.zispa.org.zw / whois.potraz.gov.zw → DNS NXDOMAIN
  - whois.co.zw → connection timeout

For the current 690-domain `.zw` engagement, this stage yields 0 contacts.
The code is kept intact because the same library/orchestrator works
correctly for `.com`/`.africa`/non-ZW TLDs — useful if scope ever expands.
================================================================
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
from lib.whois_client import is_proxy_email, lookup  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
WHOIS_PATH = data_dir() / "whois.jsonl"

SCOPES = {
    "cpanel-wp": "host_panel='cpanel' AND score>=70",
    "wp-positive": "score>=70",
    "all": "1=1",
}


def write_to_schema(conn, rec: dict) -> dict[str, int]:
    counts = {"contacts": 0, "emails": 0, "addresses": 0}
    domain = rec["domain"]
    name = rec.get("registrant_name")
    org = rec.get("registrant_org")
    country = rec.get("registrant_country")
    src = rec.get("source_provider", "whois")

    reg_email = rec.get("registrant_email")
    if reg_email and "@" in reg_email:
        conf = 0.2 if is_proxy_email(reg_email) else 0.6
        cid = upsert_contact(
            conn, domain=domain, source="whois", confidence=conf,
            display_name=name or org, role="registrant",
        )
        counts["contacts"] += 1
        if add_channel(
            conn, contact_id=cid, kind="email", value=reg_email.lower(),
            source=f"whois({src})", confidence=conf,
        ) is not None:
            counts["emails"] += 1
        if country:
            if add_channel(
                conn, contact_id=cid, kind="address", value=country,
                source=f"whois({src})", confidence=0.4,
            ) is not None:
                counts["addresses"] += 1

    admin_email = rec.get("admin_email")
    if admin_email and "@" in admin_email and admin_email != reg_email:
        conf = 0.2 if is_proxy_email(admin_email) else 0.55
        cid = upsert_contact(
            conn, domain=domain, source="whois_admin", confidence=conf,
            role="admin",
        )
        counts["contacts"] += 1
        if add_channel(
            conn, contact_id=cid, kind="email", value=admin_email.lower(),
            source=f"whois_admin({src})", confidence=conf,
        ) is not None:
            counts["emails"] += 1

    tech_email = rec.get("tech_email")
    if tech_email and "@" in tech_email and tech_email not in (reg_email, admin_email):
        conf = 0.2 if is_proxy_email(tech_email) else 0.5
        cid = upsert_contact(
            conn, domain=domain, source="whois_tech", confidence=conf,
            role="tech",
        )
        counts["contacts"] += 1
        if add_channel(
            conn, contact_id=cid, kind="email", value=tech_email.lower(),
            source=f"whois_tech({src})", confidence=conf,
        ) is not None:
            counts["emails"] += 1
    return counts


async def run(scope: str, force: bool, concurrency: int, limit: int | None) -> int:
    cfg = Config.from_env()
    conn = open_conn(DB_PATH)
    where = SCOPES.get(scope, SCOPES["cpanel-wp"])
    rows = conn.execute(
        f"SELECT domain FROM domains WHERE {where} ORDER BY (tranco_rank IS NULL), tranco_rank"
    ).fetchall()
    domains = [r["domain"] for r in rows]
    if limit:
        domains = domains[:limit]
    seen = set() if force else read_existing_keys(WHOIS_PATH, "domain")
    pending = [d for d in domains if d not in seen]
    print(f"[whois] scope={scope}  total={len(domains)}  pending={len(pending)}",
          file=sys.stderr)
    if not pending:
        return 0
    sem = asyncio.Semaphore(concurrency)
    written = 0
    totals = {"contacts": 0, "emails": 0, "addresses": 0}

    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=0.2,  # respect Whoxy free-tier 1/5s
        timeout=30,
        max_concurrent=50,
    ) as client:

        async def worker(domain: str) -> None:
            nonlocal written
            async with sem:
                try:
                    rec = await lookup(domain, client)
                except Exception as exc:
                    rec = None
                payload = rec or {}
                payload["domain"] = domain
                payload["fetched_at"] = datetime.now(timezone.utc).isoformat()
                append_record(WHOIS_PATH, payload)
                if rec:
                    c = write_to_schema(conn, payload)
                    for k in totals:
                        totals[k] += c.get(k, 0)
                written += 1
                if written % 25 == 0:
                    conn.commit()
                    print(f"[whois] {written}/{len(pending)} totals={totals}",
                          file=sys.stderr)

        await asyncio.gather(*(worker(d) for d in pending))
    conn.commit()
    conn.close()
    print(f"[whois] final totals: {totals}", file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=list(SCOPES.keys()), default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    n = asyncio.run(run(args.scope, args.force, args.concurrency, args.limit))
    print(f"[whois] wrote {n} records to {WHOIS_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
