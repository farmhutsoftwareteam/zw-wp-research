#!/usr/bin/env python3
"""Stage 19 — Pindula wiki cross-lookup.

For each cPanel WordPress domain, try to find a matching Pindula wiki page
and extract the infobox. Founder/CEO names go to contacts; phone/email go
to channels. ZW-specific enrichment.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.contacts import add_channel, open_conn, upsert_contact  # noqa: E402
from lib.http import polite_client, PoliteClient  # noqa: E402
from lib.jsonl import append_record, read_existing_keys  # noqa: E402
from lib.pindula import extract_infobox, find_page  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
PINDULA_PATH = data_dir() / "pindula.jsonl"

SCOPES = {
    "cpanel-wp": "host_panel='cpanel' AND score>=70",
    "wp-positive": "score>=70",
    "all": "1=1",
}

_PERSON_SPLIT_RE = re.compile(r"\s*[,&]\s*|\s+and\s+", re.I)
_PHONE_RE = re.compile(r"\+?\d[\d\s\-]{8,15}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def split_people(value: str) -> list[str]:
    """Split a key_people / founder value into individual names."""
    if not value:
        return []
    out = []
    for n in _PERSON_SPLIT_RE.split(value):
        n = n.strip().rstrip(",.")
        if not n or len(n) < 4 or len(n) > 80:
            continue
        # Drop pure-role rows like "CEO" / "Chairman"
        if n.lower() in {"ceo", "chairman", "managing director", "founder", "owners", "directors"}:
            continue
        out.append(n)
    return out


def write_to_schema(conn, domain: str, infobox: dict) -> dict[str, int]:
    counts = {"persons": 0, "phones": 0, "emails": 0, "addresses": 0}
    # Executives
    for key in ("key_people", "founder", "founders", "ceo", "chairperson",
                "chairman", "managing_director"):
        if key not in infobox:
            continue
        for name in split_people(infobox[key]):
            cid = upsert_contact(
                conn, domain=domain, source="pindula", confidence=0.5,
                display_name=name, role="executive",
            )
            counts["persons"] += 1
    # Phone — match anywhere in the value
    for key in ("phone", "telephone", "contact_phone"):
        v = infobox.get(key)
        if not v:
            continue
        cid = upsert_contact(
            conn, domain=domain, source="pindula", confidence=0.5,
            role="company",
        )
        for m in _PHONE_RE.findall(v):
            if add_channel(
                conn, contact_id=cid, kind="phone", value=m.strip(),
                source="pindula", confidence=0.5,
            ) is not None:
                counts["phones"] += 1
    # Email
    for key in ("email", "contact_email"):
        v = infobox.get(key)
        if not v:
            continue
        for m in _EMAIL_RE.findall(v):
            cid = upsert_contact(
                conn, domain=domain, source="pindula", confidence=0.5,
                role="company",
            )
            if add_channel(
                conn, contact_id=cid, kind="email", value=m.lower(),
                source="pindula", confidence=0.5,
            ) is not None:
                counts["emails"] += 1
    # Address / headquarters
    for key in ("headquarters", "head_office", "address", "location"):
        v = infobox.get(key)
        if not v:
            continue
        cid = upsert_contact(
            conn, domain=domain, source="pindula", confidence=0.6,
            role="company",
        )
        if add_channel(
            conn, contact_id=cid, kind="address", value=v,
            source="pindula", confidence=0.6,
        ) is not None:
            counts["addresses"] += 1
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
    seen = set() if force else read_existing_keys(PINDULA_PATH, "domain")
    pending = [d for d in domains if d not in seen]
    print(f"[pindula] scope={scope}  total={len(domains)}  pending={len(pending)}",
          file=sys.stderr)
    if not pending:
        return 0
    sem = asyncio.Semaphore(concurrency)
    written = 0
    totals = {"pages_found": 0, "persons": 0, "phones": 0, "emails": 0, "addresses": 0}

    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=4.0,
        timeout=15,
        max_concurrent=50,
    ) as client:

        async def worker(domain: str) -> None:
            nonlocal written
            async with sem:
                infobox: dict = {}
                pindula_url: str | None = None
                try:
                    pindula_url = await find_page(client, domain)
                    if pindula_url:
                        infobox = await extract_infobox(client, pindula_url)
                except Exception:
                    infobox = {}
                rec = {
                    "domain": domain,
                    "pindula_url": pindula_url,
                    "infobox": infobox,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                append_record(PINDULA_PATH, rec)
                if infobox:
                    totals["pages_found"] += 1
                    c = write_to_schema(conn, domain, infobox)
                    for k in ("persons", "phones", "emails", "addresses"):
                        totals[k] += c.get(k, 0)
                written += 1
                if written % 25 == 0:
                    conn.commit()
                    print(f"[pindula] {written}/{len(pending)} totals={totals}",
                          file=sys.stderr)

        await asyncio.gather(*(worker(d) for d in pending))
    conn.commit()
    conn.close()
    print(f"[pindula] final totals: {totals}", file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=list(SCOPES.keys()), default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    n = asyncio.run(run(args.scope, args.force, args.concurrency, args.limit))
    print(f"[pindula] wrote {n} records to {PINDULA_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
