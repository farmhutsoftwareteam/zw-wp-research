"""Contact / channel / outreach-history helpers.

Backs the lead-generation enrichment epic (see GitHub epic #10).
Every enrichment stage writes through these helpers so the schema stays
the single source of truth.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable

# ---- Stop-keyword detection ---------------------------------------------
# Keep deliberately generous on the first iteration — false positives here
# only over-suppress, which is the safe failure mode. Tighten if real users
# report being incorrectly opted out.
STOP_KEYWORDS: set[str] = {
    "stop", "unsubscribe", "remove me", "remove from list", "opt out", "opt-out",
    "no thanks", "do not contact", "take me off", "cease", "desist",
    "no more emails", "leave me alone",
}

# Shona-language variants — best-effort. Hard-suppression on these triggers
# `reason='probable_stop'` rather than direct opt-out; human review confirms.
STOP_KEYWORDS_SHONA: set[str] = {
    "rega",
    "siyana",
    "ndinokukumbira urege",
}

_STOP_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in STOP_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_STOP_RE_SHONA = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in STOP_KEYWORDS_SHONA) + r")\b",
    re.IGNORECASE,
)


def looks_like_stop_request(body_text: str) -> bool:
    """Return True if any English stop-keyword matches with word boundaries."""
    if not body_text:
        return False
    return bool(_STOP_RE.search(body_text))


def looks_like_probable_stop_shona(body_text: str) -> bool:
    """Shona-keyword match — lower confidence; needs human review."""
    if not body_text:
        return False
    return bool(_STOP_RE_SHONA.search(body_text))


# ---- Connection plumbing ------------------------------------------------
def open_conn(db_path) -> sqlite3.Connection:
    """Open a connection with the conventions every helper expects."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row
    return conn


# ---- contacts / channels ------------------------------------------------
def upsert_contact(
    conn: sqlite3.Connection,
    *,
    domain: str,
    source: str,
    confidence: float = 0.5,
    display_name: str | None = None,
    role: str | None = None,
) -> int:
    """Insert a new contact and return its id.

    Dedupe rule: if a row already exists with the same (domain, role, source,
    display_name), reuse it. This keeps repeated runs of the same enrichment
    stage idempotent without needing a unique index that would collide on
    legitimate near-duplicates (two authors with no role, for example).
    """
    cur = conn.execute(
        """
        SELECT id FROM contacts
        WHERE domain = ?
          AND COALESCE(role, '') = COALESCE(?, '')
          AND source = ?
          AND COALESCE(display_name, '') = COALESCE(?, '')
        LIMIT 1
        """,
        (domain, role, source, display_name),
    )
    row = cur.fetchone()
    if row:
        # Bump confidence if the new value is higher than what we recorded.
        conn.execute(
            "UPDATE contacts SET confidence = MAX(confidence, ?) WHERE id = ?",
            (confidence, row["id"]),
        )
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO contacts (domain, display_name, role, source, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        (domain, display_name, role, source, confidence),
    )
    return int(cur.lastrowid)


ALLOWED_CHANNEL_KINDS = {
    "email", "phone", "sms", "whatsapp",
    "twitter", "linkedin", "facebook", "instagram",
    "address", "website", "other",
}


def add_channel(
    conn: sqlite3.Connection,
    *,
    contact_id: int,
    kind: str,
    value: str,
    source: str,
    confidence: float = 0.5,
    verified: bool = False,
) -> int | None:
    """Insert a channel (or do nothing if a duplicate exists).

    Returns the id of the new row or the existing one. Returns None if the
    value is empty / falsy after stripping.
    """
    if kind not in ALLOWED_CHANNEL_KINDS:
        raise ValueError(f"unknown channel kind: {kind}")
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    conn.execute(
        """
        INSERT OR IGNORE INTO channels
            (contact_id, kind, value, source, confidence, verified)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (contact_id, kind, value, source, confidence, 1 if verified else 0),
    )
    cur = conn.execute(
        "SELECT id FROM channels WHERE contact_id = ? AND kind = ? AND value = ?",
        (contact_id, kind, value),
    )
    row = cur.fetchone()
    return int(row["id"]) if row else None


# ---- outreach_history --------------------------------------------------
ALLOWED_ACTIONS = {
    "queued", "claimed", "sent", "answered", "no_answer",
    "bounced", "replied", "opted_out", "failed",
}


class SuppressionError(RuntimeError):
    """Raised when an agent tries to record_touch on a suppressed contact."""


def record_outreach(
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
    """Append a row to outreach_history.

    Enforces consent: if the channel's value or the domain is suppressed,
    raises SuppressionError. Agents must catch + log; never retry.
    """
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unknown action: {action}")
    if action != "opted_out":
        if channel_id is not None:
            cur = conn.execute(
                "SELECT kind, value FROM channels WHERE id = ?", (channel_id,)
            )
            ch = cur.fetchone()
            if ch:
                kind, value = ch["kind"], ch["value"]
                if kind == "email" and is_suppressed(conn, email=value):
                    raise SuppressionError(f"email {value} suppressed")
                if kind in ("phone", "sms", "whatsapp") and is_suppressed(
                    conn, phone=value
                ):
                    raise SuppressionError(f"phone {value} suppressed")
        if is_suppressed(conn, domain=domain):
            raise SuppressionError(f"domain {domain} suppressed")
    cur = conn.execute(
        """
        INSERT INTO outreach_history
            (domain, contact_id, channel_id, agent, action, payload, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (domain, contact_id, channel_id, agent, action, payload, outcome),
    )
    return int(cur.lastrowid)


# ---- claims ------------------------------------------------------------
def claim_domain(
    conn: sqlite3.Connection,
    *,
    domain: str,
    agent: str,
    ttl_seconds: int = 3600,
) -> bool:
    """Atomically claim a domain for `agent` for `ttl_seconds`.

    Returns True if the claim was granted, False if another agent already
    holds an unexpired claim on this domain.
    """
    cur = conn.execute(
        """
        INSERT INTO claims (domain, agent, expires_at)
        VALUES (?, ?, datetime('now', '+' || ? || ' seconds'))
        ON CONFLICT(domain) DO UPDATE SET
            agent = excluded.agent,
            claimed_at = CURRENT_TIMESTAMP,
            expires_at = excluded.expires_at
        WHERE claims.expires_at < datetime('now')
        RETURNING 1
        """,
        (domain, agent, ttl_seconds),
    )
    row = cur.fetchone()
    return row is not None


def release_claim(conn: sqlite3.Connection, *, domain: str, agent: str) -> bool:
    """Release a claim explicitly. No-op if the claim is not ours."""
    cur = conn.execute(
        "DELETE FROM claims WHERE domain = ? AND agent = ? RETURNING 1",
        (domain, agent),
    )
    return cur.fetchone() is not None


# ---- suppressions ------------------------------------------------------
def is_suppressed(
    conn: sqlite3.Connection,
    *,
    domain: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> bool:
    """Return True if any of the identifiers is suppressed."""
    if not any((domain, email, phone)):
        return False
    clauses: list[str] = []
    params: list[Any] = []
    if domain:
        clauses.append("domain = ?")
        params.append(domain)
    if email:
        clauses.append("email = ?")
        params.append(email)
    if phone:
        clauses.append("phone = ?")
        params.append(phone)
    sql = "SELECT 1 FROM suppressions WHERE " + " OR ".join(clauses) + " LIMIT 1"
    cur = conn.execute(sql, params)
    return cur.fetchone() is not None


def suppress(
    conn: sqlite3.Connection,
    *,
    reason: str,
    source: str,
    domain: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> int | None:
    """Insert a suppression row. Idempotent on (domain, email, phone, reason).

    The CHECK constraint guarantees at least one identifier is non-null.
    """
    if not any((domain, email, phone)):
        raise ValueError("suppress() needs at least one of domain/email/phone")
    cur = conn.execute(
        """
        SELECT id FROM suppressions
        WHERE COALESCE(domain, '') = COALESCE(?, '')
          AND COALESCE(email, '')  = COALESCE(?, '')
          AND COALESCE(phone, '')  = COALESCE(?, '')
          AND reason = ?
        LIMIT 1
        """,
        (domain, email, phone, reason),
    )
    row = cur.fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO suppressions (domain, email, phone, reason, source)
        VALUES (?, ?, ?, ?, ?)
        """,
        (domain, email, phone, reason, source),
    )
    return int(cur.lastrowid)


def bulk_suppress_from_replies(
    conn: sqlite3.Connection,
    replies: Iterable[dict],
    *,
    source: str = "inbox-watcher",
) -> int:
    """Parse reply payloads and write suppressions for any stop-keyword match.

    Each reply dict should have at least one of: `from_email`, `from_phone`,
    `domain`. The matched `body` text triggers suppression.
    """
    n = 0
    for r in replies:
        body = (r.get("body") or "")
        if not body:
            continue
        if looks_like_stop_request(body):
            suppress(
                conn,
                domain=r.get("domain"),
                email=r.get("from_email"),
                phone=r.get("from_phone"),
                reason="replied_stop",
                source=source,
            )
            n += 1
        elif looks_like_probable_stop_shona(body):
            suppress(
                conn,
                domain=r.get("domain"),
                email=r.get("from_email"),
                phone=r.get("from_phone"),
                reason="probable_stop",
                source=source,
            )
            n += 1
    return n
