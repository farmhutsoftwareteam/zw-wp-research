#!/usr/bin/env python3
"""
Stage 02 — DNS resolver.

Read data/seeds.jsonl, DNS-resolve each candidate, drop dead/parked domains,
capture IP and CDN hints. Emit data/live.jsonl. Each record:

    {"domain": "techzim.co.zw", "ip": "104.21.x.x", "cdn": "cloudflare",
     "ns": ["..."], "resolved_at": "2026-..."}

Implementation notes:
- asyncio + dnspython for parallel resolution
- 1k concurrent lookups is fine; throttle if any DNS server complains
- Detect CDN by IP range (Cloudflare 104.21/172.67 ranges; Fastly; Akamai)
- Skip domains that NXDOMAIN, SERVFAIL, or resolve to known-parking IPs

Idempotent: re-runs skip domains already in live.jsonl unless --force.

TODO: implement.
"""
from __future__ import annotations
import sys


def main() -> int:
    raise NotImplementedError("Stage 02 not implemented yet — see README.md")


if __name__ == "__main__":
    sys.exit(main())
