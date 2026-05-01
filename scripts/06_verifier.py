#!/usr/bin/env python3
"""
Stage 06 — Verifier.

Read data/classified.jsonl, take top N by traffic rank (default N=100), do a
deeper Playwright pass on each:

- Render at 1440x900, screenshot to reports/screenshots/<domain>.png
- Re-confirm WP signals from rendered (not just initial) HTML
- Fingerprint plugins from /wp-content/plugins/<name>/ paths in asset URLs
- Identify active theme from /wp-content/themes/<name>/

Emit data/verified.jsonl. Each record adds:

    {..., "screenshot": "reports/screenshots/techzim.co.zw.png",
     "theme": "newspaper-x", "plugins": ["yoast-seo", "wpforms-lite", ...],
     "verified_at": "...", "homepage_kb": 142}

Concurrency cap: 5 parallel browsers (Playwright is RAM-heavy).

Idempotent: skip domains already verified unless --force.

TODO: implement.
"""
from __future__ import annotations
import sys


def main() -> int:
    raise NotImplementedError("Stage 06 not implemented yet — see README.md")


if __name__ == "__main__":
    sys.exit(main())
