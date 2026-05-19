#!/usr/bin/env python3
"""Stage 14 — Contacts schema migration + backfill.

Idempotent: creates the lead-generation tables if missing, never drops them.
Run once before any other enrichment stage (#2 onwards). Backfills the
existing `reports/cpanel_advisory.csv` into the new schema so the database
starts off with at least the homepage-scrape contacts in place.

Usage:
  python scripts/14_contacts_schema.py                     # ensure schema
  python scripts/14_contacts_schema.py --backfill          # + backfill CSV
  python scripts/14_contacts_schema.py --backfill --force  # re-run backfill
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import reports_dir  # noqa: E402
from lib.contacts import add_channel, open_conn, upsert_contact  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
ADVISORY_CSV = reports_dir() / "cpanel_advisory.csv"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    display_name TEXT,
    role TEXT,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN (
        'email','phone','sms','whatsapp',
        'twitter','linkedin','facebook','instagram',
        'address','website','other'
    )),
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    verified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS outreach_history (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    contact_id INTEGER REFERENCES contacts(id),
    channel_id INTEGER REFERENCES channels(id),
    agent TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN (
        'queued','claimed','sent','answered','no_answer',
        'bounced','replied','opted_out','failed'
    )),
    payload TEXT,
    outcome TEXT,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS claims (
    domain TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppressions (
    id INTEGER PRIMARY KEY,
    domain TEXT,
    email TEXT,
    phone TEXT,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (domain IS NOT NULL OR email IS NOT NULL OR phone IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_contacts_domain ON contacts(domain);
CREATE INDEX IF NOT EXISTS idx_contacts_source ON contacts(source);
CREATE INDEX IF NOT EXISTS idx_channels_contact ON channels(contact_id);
CREATE INDEX IF NOT EXISTS idx_channels_kind ON channels(kind, value);
CREATE INDEX IF NOT EXISTS idx_outreach_domain_time ON outreach_history(domain, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_claims_expires ON claims(expires_at);
CREATE INDEX IF NOT EXISTS idx_suppressions_domain ON suppressions(domain) WHERE domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_suppressions_email ON suppressions(email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_suppressions_phone ON suppressions(phone) WHERE phone IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_value_per_contact
    ON channels(contact_id, kind, value);

-- Enrichment 2.0 (stages 23–29): freshness, SSL, domain-age, perf, vulns
CREATE TABLE IF NOT EXISTS pagespeed (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    strategy TEXT NOT NULL CHECK(strategy IN ('mobile','desktop')),
    performance REAL,
    accessibility REAL,
    best_practices REAL,
    seo REAL,
    lcp_ms INTEGER,
    cls REAL,
    tbt_ms INTEGER,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pagespeed_domain ON pagespeed(domain);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pagespeed_per_strategy
    ON pagespeed(domain, strategy);

CREATE TABLE IF NOT EXISTS vulnerabilities (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    component_type TEXT NOT NULL CHECK(component_type IN ('wp_core','plugin','theme')),
    component TEXT NOT NULL,
    version TEXT,
    cve TEXT,
    title TEXT,
    severity TEXT,
    fixed_in TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_vuln_domain ON vulnerabilities(domain);
CREATE UNIQUE INDEX IF NOT EXISTS uq_vuln_unique
    ON vulnerabilities(domain, component_type, component, cve);

CREATE TABLE IF NOT EXISTS freshness (
    domain TEXT PRIMARY KEY,
    last_post_at TEXT,
    posts_last_90d INTEGER,
    feed_url TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ssl_expiry (
    domain TEXT PRIMARY KEY,
    not_before TEXT,
    not_after TEXT,
    days_remaining INTEGER,
    issuer_cn TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ssl_days ON ssl_expiry(days_remaining);

CREATE TABLE IF NOT EXISTS domain_age (
    domain TEXT PRIMARY KEY,
    first_archived_at TEXT,
    snapshots_count INTEGER,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create lead-gen tables + indexes if missing. Never drops anything."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _classify_social(url: str) -> str | None:
    """Map a social URL to the channel kind name."""
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None
    if "twitter.com" in host or "x.com" in host:
        return "twitter"
    if "facebook.com" in host or "fb.com" in host:
        return "facebook"
    if "linkedin.com" in host:
        return "linkedin"
    if "instagram.com" in host:
        return "instagram"
    return None


def backfill_from_advisory_csv(conn: sqlite3.Connection, path: Path) -> dict[str, int]:
    """Backfill contacts/channels from the existing stage-10 CSV.

    Idempotent — repeated runs do not duplicate rows (relies on the unique
    index on (contact_id, kind, value) + upsert_contact dedup).
    """
    counts = {"contacts": 0, "emails": 0, "phones": 0, "socials": 0, "addresses": 0}
    if not path.exists():
        return counts
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            domain = (row.get("domain") or "").strip()
            primary = (row.get("primary_contact_email") or "").strip()
            all_emails = [e.strip() for e in (row.get("all_emails") or "").split(";") if e.strip()]
            phones = [p.strip() for p in (row.get("phones") or "").split(";") if p.strip()]
            socials = [s.strip() for s in (row.get("socials") or "").split(";") if s.strip()]
            addresses = [a.strip() for a in (row.get("addresses") or "").split(";") if a.strip()]
            if not domain:
                continue
            if not (primary or all_emails or phones or socials or addresses):
                continue
            contact_id = upsert_contact(
                conn,
                domain=domain,
                source="homepage_scrape",
                confidence=0.6,
                role=None,
            )
            counts["contacts"] += 1
            for email in {primary, *all_emails}:
                if email:
                    if add_channel(
                        conn,
                        contact_id=contact_id,
                        kind="email",
                        value=email.lower(),
                        source="homepage_scrape",
                        confidence=0.6 if email == primary else 0.5,
                    ) is not None:
                        counts["emails"] += 1
            for phone in phones:
                if add_channel(
                    conn,
                    contact_id=contact_id,
                    kind="phone",
                    value=phone,
                    source="homepage_scrape",
                    confidence=0.5,
                ) is not None:
                    counts["phones"] += 1
            for s in socials:
                kind = _classify_social(s)
                if kind and add_channel(
                    conn,
                    contact_id=contact_id,
                    kind=kind,
                    value=s,
                    source="homepage_scrape",
                    confidence=0.4,
                ) is not None:
                    counts["socials"] += 1
            for addr in addresses:
                if add_channel(
                    conn,
                    contact_id=contact_id,
                    kind="address",
                    value=addr,
                    source="homepage_scrape",
                    confidence=0.5,
                ) is not None:
                    counts["addresses"] += 1
    conn.commit()
    return counts


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(DB_PATH))
    p.add_argument("--backfill", action="store_true",
                   help="Backfill from reports/cpanel_advisory.csv after schema")
    p.add_argument("--csv", default=str(ADVISORY_CSV))
    p.add_argument("--force", action="store_true",
                   help="(reserved) — backfill is naturally idempotent")
    args = p.parse_args()
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_conn(db_path)
    ensure_schema(conn)
    print(f"[schema] tables present in {db_path}", file=sys.stderr)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('contacts','channels','outreach_history','claims','suppressions') "
        "ORDER BY name"
    )
    for row in cur.fetchall():
        print(f"  - {row['name']}", file=sys.stderr)
    if args.backfill:
        counts = backfill_from_advisory_csv(conn, Path(args.csv))
        print(f"[schema] backfill: {counts}", file=sys.stderr)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
