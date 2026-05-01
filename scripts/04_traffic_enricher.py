#!/usr/bin/env python3
"""
Stage 04 — Traffic enricher.

Read data/detections.jsonl (filter score >= 70), cross-reference each domain
against traffic-rank sources. Emit data/enriched.jsonl. Each record:

    {"domain": "techzim.co.zw", "score": 95, "tranco_rank": 42173,
     "cf_radar_bucket": "10k-100k", "similarweb_estimate_visits": 850000, ...}

Sources (cheapest-first):
- Tranco list (cached CSV, free, lookup by domain)
- Cloudflare Radar API (free for our scale)
- similarweb (paid; only call for top 200 by Tranco rank to limit cost)

Idempotent: skip domains already enriched unless --force.

TODO: implement.
"""
from __future__ import annotations
import sys


def main() -> int:
    raise NotImplementedError("Stage 04 not implemented yet — see README.md")


if __name__ == "__main__":
    sys.exit(main())
