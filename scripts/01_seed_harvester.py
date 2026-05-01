#!/usr/bin/env python3
"""
Stage 01 — Seed harvester.

Pull candidate domains from every available source and emit a single deduped
JSONL file at data/seeds.jsonl. Each record:

    {"domain": "techzim.co.zw", "source": "tranco", "hint": "rank=42173"}

Sources to implement (run as parallel agents/tasks where possible):
- ZICTA / .zw registry pages (scrape)
- Tranco top 1M (filter by .zw TLD; download once, cached)
- Cloudflare Radar top sites by country (Radar API)
- HTTP Archive on BigQuery: SELECT url FROM ... WHERE country_code="ZW"
- BuiltWith API (paid; if key present in .env)
- Common Crawl CDX (filter host suffix .zw)
- Curated scrapes: techzim.co.zw, pindula.co.zw, gov department lists
- Geo-clue sweep: search for ZWL/ZAR pricing, +263 phone, addressCountry: ZW

Idempotent: re-running merges new records into seeds.jsonl without duplicating
existing (domain, source) pairs.

TODO: implement.
"""
from __future__ import annotations
import sys


def main() -> int:
    raise NotImplementedError("Stage 01 not implemented yet — see README.md")


if __name__ == "__main__":
    sys.exit(main())
