"""Qualified-leads view installer + scoring helper.

The view `qualified_leads` joins the enrichment tables into a single ranked
table that downstream agents (Vapi, ElevenLabs, Gmail) + the dashboard
consume as the day's call list.

Scoring is straightforward, weighted-sum:
  + Has any phone               +20
  + Has high-confidence email   +20
  + Has a named human           +15
  + Active in last 90 days      +15
  + Outdated WP / known CVE     +10
  + SSL expiring in 30 days     +10
  + Has Tranco rank             +5
  + Has Pindula entry           +5
"""
from __future__ import annotations

import sqlite3


VIEW_SQL = r"""
DROP VIEW IF EXISTS qualified_leads;
CREATE VIEW qualified_leads AS
WITH
phone_per_domain AS (
    SELECT c.domain,
           MAX(ch.value) AS phone,
           MAX(ch.confidence) AS phone_confidence
    FROM contacts c JOIN channels ch ON ch.contact_id = c.id
    WHERE ch.kind = 'phone'
    GROUP BY c.domain
),
email_per_domain AS (
    SELECT c.domain,
           MAX(CASE WHEN ch.confidence >= 0.6 THEN ch.value END) AS email_hc,
           MAX(ch.value) AS email_any,
           MAX(ch.confidence) AS email_confidence
    FROM contacts c JOIN channels ch ON ch.contact_id = c.id
    WHERE ch.kind = 'email'
    GROUP BY c.domain
),
named_per_domain AS (
    SELECT c.domain, MAX(c.display_name) AS display_name
    FROM contacts c
    WHERE c.display_name IS NOT NULL AND c.display_name <> ''
    GROUP BY c.domain
),
vuln_per_domain AS (
    SELECT domain, COUNT(*) AS vuln_count
    FROM vulnerabilities
    GROUP BY domain
),
psi_mobile AS (
    SELECT domain, performance AS perf_mobile
    FROM pagespeed WHERE strategy='mobile'
)
SELECT
    d.domain,
    d.tranco_rank,
    d.category,
    d.host_panel,
    d.mx_provider,
    d.wp_version,
    p.phone,
    e.email_hc,
    e.email_any,
    n.display_name,
    f.last_post_at,
    f.posts_last_90d,
    s.days_remaining AS ssl_days,
    s.not_after AS ssl_expires_at,
    da.first_archived_at,
    v.vuln_count,
    pm.perf_mobile,
    -- Score: integer 0..100
    (
        CASE WHEN p.phone IS NOT NULL THEN 20 ELSE 0 END +
        CASE WHEN e.email_hc IS NOT NULL THEN 20 ELSE 0 END +
        CASE WHEN n.display_name IS NOT NULL THEN 15 ELSE 0 END +
        CASE WHEN COALESCE(f.posts_last_90d,0) > 0 THEN 15 ELSE 0 END +
        CASE WHEN COALESCE(v.vuln_count,0) > 0 THEN 10 ELSE 0 END +
        CASE WHEN s.days_remaining IS NOT NULL AND s.days_remaining BETWEEN 0 AND 30 THEN 10 ELSE 0 END +
        CASE WHEN d.tranco_rank IS NOT NULL THEN 5 ELSE 0 END +
        CASE WHEN EXISTS(SELECT 1 FROM contacts c WHERE c.domain=d.domain AND c.source='pindula') THEN 5 ELSE 0 END
    ) AS lead_score,
    -- Suppression check
    CASE WHEN EXISTS(
        SELECT 1 FROM suppressions sp
        WHERE sp.domain=d.domain
           OR sp.email IN (e.email_hc, e.email_any)
           OR sp.phone = p.phone
    ) THEN 1 ELSE 0 END AS suppressed,
    -- Was already contacted by an agent?
    (SELECT COUNT(*) FROM outreach_history h
     WHERE h.domain=d.domain AND h.action IN ('sent','answered','no_answer','bounced','replied','opted_out'))
        AS prior_touches
FROM domains d
LEFT JOIN phone_per_domain p ON p.domain = d.domain
LEFT JOIN email_per_domain e ON e.domain = d.domain
LEFT JOIN named_per_domain n ON n.domain = d.domain
LEFT JOIN freshness f        ON f.domain = d.domain
LEFT JOIN ssl_expiry s       ON s.domain = d.domain
LEFT JOIN domain_age da      ON da.domain = d.domain
LEFT JOIN vuln_per_domain v  ON v.domain = d.domain
LEFT JOIN psi_mobile pm      ON pm.domain = d.domain
WHERE d.host_panel='cpanel' AND d.score>=70
ORDER BY lead_score DESC, (d.tranco_rank IS NULL), d.tranco_rank;
"""


def ensure_view(conn: sqlite3.Connection, *, force: bool = False) -> None:
    """Install the qualified_leads view if not already present.

    `force=True` drops and recreates (use when the SQL definition changes).
    Default skips the drop to avoid contending with concurrent writers.
    """
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("SELECT 1 FROM qualified_leads LIMIT 0")
        if not force:
            return
    except sqlite3.OperationalError:
        pass
    conn.executescript(VIEW_SQL)
    conn.commit()


def pain_signals(row: dict) -> list[str]:
    """Return human-readable pain-point bullets for a qualified-leads row."""
    out: list[str] = []
    if row.get("vuln_count") and row["vuln_count"] > 0:
        out.append(
            f"{row['vuln_count']} known plugin/theme vulnerabilit"
            + ("y" if row['vuln_count'] == 1 else "ies")
            + " on the site"
        )
    if row.get("ssl_days") is not None:
        if row["ssl_days"] < 0:
            out.append(f"SSL certificate EXPIRED {-row['ssl_days']} days ago")
        elif row["ssl_days"] <= 30:
            out.append(f"SSL certificate expires in {row['ssl_days']} days")
    if row.get("perf_mobile") is not None and row["perf_mobile"] < 50:
        out.append(
            f"Mobile PageSpeed score is {int(row['perf_mobile'])}/100 "
            "(slow for visitors on phones)"
        )
    if (row.get("wp_version") or "").startswith(("5.", "4.")):
        out.append(
            f"Running WordPress {row['wp_version']} — multiple versions "
            "behind current GA"
        )
    if row.get("last_post_at"):
        # Crude staleness check
        try:
            from datetime import datetime, timezone
            lp = datetime.fromisoformat(row["last_post_at"].replace("Z", "+00:00"))
            days = (datetime.now(timezone.utc) - lp).days
            if days > 365:
                out.append(f"No new content in {days} days — site looks dormant")
        except Exception:
            pass
    if row.get("host_panel") == "cpanel":
        out.append(
            "Hosted on cPanel — currently in scope for CVE-2026-41940 incident "
            "response (pre-auth bypass)"
        )
    return out
