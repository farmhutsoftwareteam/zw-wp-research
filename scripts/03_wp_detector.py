#!/usr/bin/env python3
"""
Stage 03 — WordPress detector.

Read data/live.jsonl, for each domain fetch homepage + 4 probe paths, score
WordPress signals 0-100. Emit data/detections.jsonl. Each record:

    {"domain": "techzim.co.zw", "score": 95, "signals": {...},
     "wp_version": "6.4.2", "homepage_status": 200, "checked_at": "..."}

Probes (with reasonable timeouts and 1 req/sec/host max):
- GET /                             — homepage HTML
- GET /wp-json/                     — REST API
- GET /feed/                        — RSS
- GET /wp-login.php                 — admin login
- GET /readme.html                  — old WP installs leave this

Signals scored (weighted):
- meta generator tag = WordPress    → +30
- /wp-json/ returns valid WP JSON   → +25
- Link header rel=https://api.w.org → +20
- /wp-content/ or /wp-includes/ in HTML → +15 each (max +20)
- /feed/ has WP generator tag       → +10
- /wp-login.php returns 200 with WP HTML → +10
- Theme/plugin path in <link>/<script> → +5 each (max +10)

Score >= 70 = strong WP. Cap at 100.

Sharding: --shard 0/8 ... --shard 7/8 to fan out across 8 parallel workers.
Each worker only processes domains where hash(domain) % 8 == shard_index.

Idempotent: skip domains already in detections.jsonl unless --force.

TODO: implement.
"""
from __future__ import annotations
import sys


def main() -> int:
    raise NotImplementedError("Stage 03 not implemented yet — see README.md")


if __name__ == "__main__":
    sys.exit(main())
