"""Agent-facing contact API.

The thin layer between the SQLite database and downstream outreach agents
(Gmail MCP, Vapi, ElevenLabs). Exposes:

  - `next_unclaimed_domain`   atomically claims a domain and returns its contact bundle
  - `release_claim`           release a claim explicitly
  - `record_touch`            log an outreach action (respects suppressions)
  - `peek_unclaimed`          read-only preview of what would be claimed next

The actual write-locking lives in SQLite's `claims` table. Multiple agents
running on the same machine can call these helpers safely.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from .contacts import SuppressionError, is_suppressed, open_conn, record_outreach


VIEW_SQL = """
CREATE VIEW IF NOT EXISTS contacts_for_agent AS
SELECT
    d.domain, d.tranco_rank, d.category, d.host_panel,
    d.mx_provider,
    c.id AS contact_id, c.display_name, c.role,
    c.source AS contact_source, c.confidence AS contact_confidence,
    ch.id AS channel_id, ch.kind, ch.value,
    ch.source AS channel_source, ch.confidence AS channel_confidence, ch.verified,
    (SELECT COUNT(*) FROM outreach_history h WHERE h.domain=d.domain) AS prior_touches,
    (SELECT MAX(occurred_at) FROM outreach_history h WHERE h.domain=d.domain) AS last_touch_at,
    CASE WHEN EXISTS(
        SELECT 1 FROM suppressions s
        WHERE s.domain=d.domain OR s.email=ch.value OR s.phone=ch.value
    ) THEN 1 ELSE 0 END AS suppressed
FROM domains d
LEFT JOIN contacts c ON c.domain = d.domain
LEFT JOIN channels ch ON ch.contact_id = c.id
WHERE d.host_panel = 'cpanel' AND d.score >= 70
ORDER BY (d.tranco_rank IS NULL), d.tranco_rank, c.confidence DESC, ch.confidence DESC;
"""


def ensure_view(conn: sqlite3.Connection) -> None:
    """Install the contacts_for_agent view. Idempotent."""
    # 30s busy-timeout — works around writers (stage 18 etc.) holding the lock
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.executescript(VIEW_SQL)
    conn.commit()


def _bundle_domain(conn: sqlite3.Connection, domain: str) -> dict[str, Any]:
    """Build the contact payload for one domain — multiple contacts, each
    with multiple channels."""
    rows = conn.execute(
        """
        SELECT contact_id, display_name, role, contact_source, contact_confidence,
               channel_id, kind, value, channel_source, channel_confidence, verified,
               prior_touches, last_touch_at, suppressed,
               category, host_panel, mx_provider, tranco_rank
        FROM contacts_for_agent
        WHERE domain = ?
        ORDER BY contact_confidence DESC, channel_confidence DESC
        """,
        (domain,),
    ).fetchall()
    if not rows:
        return {"domain": domain, "contacts": [], "prior_touches": 0}
    contacts_map: dict[int, dict] = {}
    meta = rows[0]
    for r in rows:
        cid = r["contact_id"]
        if cid is None:
            continue
        if cid not in contacts_map:
            contacts_map[cid] = {
                "contact_id": cid,
                "display_name": r["display_name"],
                "role": r["role"],
                "source": r["contact_source"],
                "confidence": r["contact_confidence"],
                "channels": [],
            }
        if r["channel_id"] is not None:
            contacts_map[cid]["channels"].append({
                "channel_id": r["channel_id"],
                "kind": r["kind"],
                "value": r["value"],
                "source": r["channel_source"],
                "confidence": r["channel_confidence"],
                "verified": bool(r["verified"]),
            })
    return {
        "domain": domain,
        "category": meta["category"],
        "host_panel": meta["host_panel"],
        "mx_provider": meta["mx_provider"],
        "tranco_rank": meta["tranco_rank"],
        "prior_touches": meta["prior_touches"],
        "last_touch_at": meta["last_touch_at"],
        "contacts": list(contacts_map.values()),
    }


def peek_unclaimed(
    conn: sqlite3.Connection,
    *,
    prefer_channel: str | None = None,
    max_prior_touches: int = 0,
    n: int = 5,
) -> list[dict]:
    """Return up to `n` candidate domains without claiming any."""
    where = [
        "d.host_panel='cpanel'", "d.score>=70",
        "NOT EXISTS (SELECT 1 FROM claims cl WHERE cl.domain=d.domain "
        "  AND cl.expires_at > datetime('now'))",
        "NOT EXISTS (SELECT 1 FROM suppressions s WHERE s.domain=d.domain)",
        "(SELECT COUNT(*) FROM outreach_history h WHERE h.domain=d.domain) <= ?",
    ]
    params: list[Any] = [max_prior_touches]
    if prefer_channel:
        where.append(
            "EXISTS (SELECT 1 FROM contacts c JOIN channels ch ON ch.contact_id=c.id "
            "  WHERE c.domain=d.domain AND ch.kind=?)"
        )
        params.append(prefer_channel)
    sql = (
        "SELECT d.domain FROM domains d WHERE "
        + " AND ".join(where)
        + " ORDER BY (d.tranco_rank IS NULL), d.tranco_rank LIMIT ?"
    )
    params.append(n)
    rows = conn.execute(sql, params).fetchall()
    return [_bundle_domain(conn, r["domain"]) for r in rows]


def next_unclaimed_domain(
    conn: sqlite3.Connection,
    *,
    agent: str,
    prefer_channel: str | None = None,
    max_prior_touches: int = 0,
    ttl_seconds: int = 3600,
) -> dict | None:
    """Atomically pick + claim the next unclaimed in-scope domain."""
    where = [
        "d.host_panel='cpanel'", "d.score>=70",
        "NOT EXISTS (SELECT 1 FROM claims cl WHERE cl.domain=d.domain "
        "  AND cl.expires_at > datetime('now'))",
        "NOT EXISTS (SELECT 1 FROM suppressions s WHERE s.domain=d.domain)",
        "(SELECT COUNT(*) FROM outreach_history h WHERE h.domain=d.domain) <= ?",
    ]
    params: list[Any] = []
    if prefer_channel:
        where.append(
            "EXISTS (SELECT 1 FROM contacts c JOIN channels ch ON ch.contact_id=c.id "
            "  WHERE c.domain=d.domain AND ch.kind=?)"
        )
        params.append(prefer_channel)
    params.append(max_prior_touches)
    # SQLite doesn't support INSERT ... SELECT ... ORDER BY ... LIMIT ...
    # RETURNING reliably across versions. So: SELECT a candidate, then try
    # the conditional insert. Retry up to 8 times if we lose the race or
    # hit a lock (another enrichment stage may be writing).
    last_error: Exception | None = None
    for attempt in range(8):
        try:
            sql = (
                "SELECT d.domain FROM domains d WHERE "
                + " AND ".join(where)
                + " ORDER BY (d.tranco_rank IS NULL), d.tranco_rank LIMIT 1"
            )
            row = conn.execute(sql, params).fetchone()
            if not row:
                return None
            domain = row["domain"]
            # Atomic insert: only succeeds if no live claim exists
            conn.execute(
                """
                INSERT INTO claims (domain, agent, expires_at)
                VALUES (?, ?, datetime('now', '+' || ? || ' seconds'))
                ON CONFLICT(domain) DO UPDATE SET
                    agent = excluded.agent,
                    claimed_at = CURRENT_TIMESTAMP,
                    expires_at = excluded.expires_at
                WHERE claims.expires_at < datetime('now')
                """,
                (domain, agent, ttl_seconds),
            )
            conn.commit()
            # Verify we are the holder now (handles the case where the
            # ON CONFLICT WHERE didn't fire because the existing claim hasn't expired).
            held = conn.execute(
                "SELECT agent, expires_at FROM claims WHERE domain = ?",
                (domain,),
            ).fetchone()
            if not held or held["agent"] != agent:
                continue  # someone else has it; retry
            record_outreach(conn, domain=domain, agent=agent, action="claimed")
            conn.commit()
            payload = _bundle_domain(conn, domain)
            return payload
        except sqlite3.IntegrityError as exc:
            last_error = exc
            continue
        except sqlite3.OperationalError as exc:
            # `database is locked` — another writer holds the lock; back off
            last_error = exc
            time.sleep(min(0.25 * (2 ** attempt), 4.0))
            continue
    if last_error:
        raise last_error
    return None


def release_claim(
    conn: sqlite3.Connection,
    *,
    domain: str,
    agent: str,
) -> bool:
    cur = conn.execute(
        "DELETE FROM claims WHERE domain = ? AND agent = ?",
        (domain, agent),
    )
    conn.commit()
    return cur.rowcount > 0


def record_touch(
    conn: sqlite3.Connection,
    *,
    domain: str,
    agent: str,
    action: str,
    contact_id: int | None = None,
    channel_id: int | None = None,
    payload: str | None = None,
    outcome: str | None = None,
) -> int:
    """Log an outreach action.

    Raises SuppressionError if the domain or the channel value matches the
    suppressions list and the action is not 'opted_out'.
    """
    rid = record_outreach(
        conn,
        domain=domain,
        agent=agent,
        action=action,
        contact_id=contact_id,
        channel_id=channel_id,
        payload=payload,
        outcome=outcome,
    )
    conn.commit()
    return rid


def history(conn: sqlite3.Connection, *, domain: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, contact_id, channel_id, agent, action, payload, outcome,
               occurred_at
        FROM outreach_history
        WHERE domain = ?
        ORDER BY occurred_at DESC
        LIMIT ?
        """,
        (domain, limit),
    ).fetchall()
    return [dict(r) for r in rows]
