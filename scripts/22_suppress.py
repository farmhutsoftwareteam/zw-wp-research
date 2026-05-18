#!/usr/bin/env python3
"""Stage 22 — Manual suppression CLI.

Operate the consent / opt-out store from the command line. The schema for
`suppressions` lives in scripts/14_contacts_schema.py; this file is the
human entry point. The Gmail / Vapi / ElevenLabs agents use the
`lib.contacts.is_suppressed` and `lib.contacts.suppress` library helpers
to read/write the same table.

Usage:
  python scripts/22_suppress.py --add --domain x.co.zw --reason manual --source munya
  python scripts/22_suppress.py --add --email john@x.co.zw --reason replied_stop --source gmail-agent
  python scripts/22_suppress.py --add --phone "+263 77 123 4567" --reason replied_stop --source vapi
  python scripts/22_suppress.py --remove --domain x.co.zw
  python scripts/22_suppress.py --list
  python scripts/22_suppress.py --list --reason replied_stop
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import reports_dir  # noqa: E402
from lib.contacts import is_suppressed, open_conn, suppress  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"


def cmd_add(args) -> int:
    if not any((args.domain, args.email, args.phone)):
        print("--add needs at least one of --domain/--email/--phone", file=sys.stderr)
        return 2
    conn = open_conn(DB_PATH)
    suppress(
        conn,
        domain=args.domain,
        email=args.email,
        phone=args.phone,
        reason=args.reason,
        source=args.source,
    )
    conn.commit()
    conn.close()
    target = args.domain or args.email or args.phone
    print(f"suppressed {target!r} (reason={args.reason!r}, source={args.source!r})")
    return 0


def cmd_remove(args) -> int:
    if not any((args.domain, args.email, args.phone)):
        print("--remove needs --domain/--email/--phone", file=sys.stderr)
        return 2
    target_repr = args.domain or args.email or args.phone
    if not args.yes:
        reply = input(f"Remove suppression for {target_repr!r}? Type 'yes' to confirm: ").strip()
        if reply != "yes":
            print("aborted")
            return 1
    conn = open_conn(DB_PATH)
    clauses, params = [], []
    if args.domain:
        clauses.append("domain = ?")
        params.append(args.domain)
    if args.email:
        clauses.append("email = ?")
        params.append(args.email)
    if args.phone:
        clauses.append("phone = ?")
        params.append(args.phone)
    cur = conn.execute(
        f"DELETE FROM suppressions WHERE {' OR '.join(clauses)} RETURNING id",
        params,
    )
    rows = cur.fetchall()
    conn.commit()
    conn.close()
    print(f"removed {len(rows)} suppression row(s) for {target_repr!r}")
    return 0


def cmd_list(args) -> int:
    conn = open_conn(DB_PATH)
    if args.reason:
        cur = conn.execute(
            "SELECT id, domain, email, phone, reason, source, created_at "
            "FROM suppressions WHERE reason = ? ORDER BY created_at DESC",
            (args.reason,),
        )
    else:
        cur = conn.execute(
            "SELECT id, domain, email, phone, reason, source, created_at "
            "FROM suppressions ORDER BY created_at DESC"
        )
    rows = cur.fetchall()
    if not rows:
        print("(no suppressions)")
        return 0
    width_d = max(7, max(len(r["domain"] or "") for r in rows))
    width_e = max(7, max(len(r["email"] or "") for r in rows))
    width_p = max(7, max(len(r["phone"] or "") for r in rows))
    print(f"{'id':>4}  {'domain':<{width_d}}  {'email':<{width_e}}  "
          f"{'phone':<{width_p}}  {'reason':<14}  source  created_at")
    for r in rows:
        print(
            f"{r['id']:>4}  {(r['domain'] or '—'):<{width_d}}  "
            f"{(r['email'] or '—'):<{width_e}}  {(r['phone'] or '—'):<{width_p}}  "
            f"{r['reason']:<14}  {r['source']}  {r['created_at']}"
        )
    return 0


def cmd_check(args) -> int:
    conn = open_conn(DB_PATH)
    res = is_suppressed(conn, domain=args.domain, email=args.email, phone=args.phone)
    target = args.domain or args.email or args.phone
    print(f"{target!r}: {'SUPPRESSED' if res else 'not suppressed'}")
    return 0 if not res else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--add", action="store_true")
    g.add_argument("--remove", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument("--check", action="store_true")
    p.add_argument("--domain")
    p.add_argument("--email")
    p.add_argument("--phone")
    p.add_argument("--reason", default="manual")
    p.add_argument("--source", default="munya")
    p.add_argument("--yes", action="store_true",
                   help="skip confirmation prompt on --remove")
    args = p.parse_args()
    if args.add:
        return cmd_add(args)
    if args.remove:
        return cmd_remove(args)
    if args.list:
        return cmd_list(args)
    if args.check:
        return cmd_check(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
