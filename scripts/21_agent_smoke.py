#!/usr/bin/env python3
"""Stage 21 — Agent API smoke / sanity check.

Operates the agent-facing API from the command line. Useful for verifying
the schema is right before wiring a real Gmail / Vapi / ElevenLabs agent.

  python scripts/21_agent_smoke.py --peek
  python scripts/21_agent_smoke.py --claim --agent test
  python scripts/21_agent_smoke.py --release foo.co.zw --agent test
  python scripts/21_agent_smoke.py --history foo.co.zw
  python scripts/21_agent_smoke.py --stats
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.agent_api import (  # noqa: E402
    ensure_view, history, next_unclaimed_domain, peek_unclaimed, release_claim,
)
from lib.config import reports_dir  # noqa: E402
from lib.contacts import open_conn  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--peek", action="store_true")
    g.add_argument("--claim", action="store_true")
    g.add_argument("--release")
    g.add_argument("--history")
    g.add_argument("--stats", action="store_true")
    p.add_argument("--agent", default="smoke-test")
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--prefer-channel", default=None,
                   help="email | phone | twitter | ... — only domains with that channel")
    p.add_argument("--max-prior-touches", type=int, default=0)
    p.add_argument("--ttl", type=int, default=3600)
    args = p.parse_args()

    conn = open_conn(DB_PATH)
    ensure_view(conn)

    if args.peek:
        bundles = peek_unclaimed(
            conn,
            prefer_channel=args.prefer_channel,
            max_prior_touches=args.max_prior_touches,
            n=args.n,
        )
        for b in bundles:
            print(json.dumps(b, indent=2, default=str))
        print(f"\n# {len(bundles)} candidates (claim-free preview)", file=sys.stderr)
        return 0

    if args.claim:
        payload = next_unclaimed_domain(
            conn,
            agent=args.agent,
            prefer_channel=args.prefer_channel,
            max_prior_touches=args.max_prior_touches,
            ttl_seconds=args.ttl,
        )
        if payload is None:
            print("# no candidates available", file=sys.stderr)
            return 1
        print(json.dumps(payload, indent=2, default=str))
        print(f"\n# claimed by {args.agent!r} for {args.ttl}s — release with --release {payload['domain']} --agent {args.agent}",
              file=sys.stderr)
        return 0

    if args.release:
        ok = release_claim(conn, domain=args.release, agent=args.agent)
        print(f"# released {args.release!r}: {'yes' if ok else 'no'}")
        return 0 if ok else 1

    if args.history:
        rows = history(conn, domain=args.history)
        for r in rows:
            print(json.dumps(r, default=str))
        return 0

    if args.stats:
        cur = conn.execute(
            """
            SELECT
              (SELECT COUNT(DISTINCT domain) FROM contacts_for_agent) AS scope,
              (SELECT COUNT(DISTINCT domain) FROM contacts_for_agent WHERE suppressed=0) AS not_suppressed,
              (SELECT COUNT(*) FROM claims WHERE expires_at > datetime('now')) AS active_claims,
              (SELECT COUNT(*) FROM outreach_history) AS history_rows,
              (SELECT COUNT(*) FROM suppressions) AS suppressions
            """
        )
        row = cur.fetchone()
        print(json.dumps(dict(row), indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
