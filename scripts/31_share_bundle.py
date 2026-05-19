#!/usr/bin/env python3
"""Stage 31 — Build a self-contained share bundle for an engagement colleague.

Bundles the artifacts a teammate needs to work the lead list without
running the pipeline:
  - reports/pitch_html/*               (the 50 pitch cards as browsable HTML)
  - reports/cpanel_advisory.csv        (full contact list, 690 rows)
  - reports/qualified_leads.csv        (the SQL view exported as CSV)
  - reports/zwwp.db                    (the SQLite DB for deeper queries)
  - share/OPEN_THIS_FIRST.html         (top-level index linking everything)
  - share/README.txt                   (plain-text instructions)

Output goes to `reports/share/`, then zipped to
`reports/share-bundle-<date>.zip` for upload to Drive / WhatsApp / WeTransfer.

Nothing in this bundle is on the public Vercel site — it's all gitignored.
The bundle contains personal contact data; treat the zip as confidential.
"""
from __future__ import annotations

import csv
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import reports_dir  # noqa: E402
from lib.contacts import open_conn  # noqa: E402
from lib.qualified import ensure_view  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
PITCH_HTML_DIR = reports_dir() / "pitch_html"
ADVISORY_CSV = reports_dir() / "cpanel_advisory.csv"
SHARE_DIR = reports_dir() / "share"
QUALIFIED_CSV = SHARE_DIR / "qualified_leads.csv"


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ZW WP cPanel — engagement kit (PRIVATE)</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<style>
*{{box-sizing:border-box}}
body{{margin:0;font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;color:#1a1a1a;background:#fafafa}}
.banner{{background:#fff3e0;border-bottom:2px solid #f57c00;padding:10px 24px;font-size:13px}}
.banner strong{{color:#bf360c}}
.wrap{{max-width:880px;margin:0 auto;padding:24px}}
h1{{font-size:24px;margin:0 0 6px;font-weight:700}}
h2{{font-size:18px;margin:28px 0 10px;border-bottom:1px solid #e0e0e0;padding-bottom:4px}}
p{{margin:0 0 12px}}
a{{color:#21759b;text-decoration:none}}
a:hover{{text-decoration:underline}}
.card{{background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 18px;margin:10px 0}}
.card a.title{{font-size:16px;font-weight:600}}
.card .meta{{color:#666;font-size:12px;margin-top:4px}}
code{{background:#f0f0f0;padding:1px 6px;border-radius:3px;font-size:12.5px}}
.stat{{display:inline-block;background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:8px 14px;margin:0 8px 8px 0;min-width:100px}}
.stat .num{{display:block;font-size:18px;font-weight:600}}
.stat .lbl{{display:block;color:#666;font-size:11px}}
</style>
</head>
<body>
<div class="banner">
  <strong>PRIVATE / CONFIDENTIAL</strong> — engagement kit, contains personal contact data.
  Do not redistribute. Do not upload to a public host.
</div>
<div class="wrap">
  <h1>ZW WP cPanel — engagement kit</h1>
  <p>Generated {generated_at}. This bundle is everything you need to run outreach against the 690 Zimbabwean cPanel-hosted WordPress sites we identified.</p>

  <h2>At a glance</h2>
  <div>
    <div class="stat"><span class="num">{total}</span><span class="lbl">total leads</span></div>
    <div class="stat"><span class="num">{with_email}</span><span class="lbl">with email</span></div>
    <div class="stat"><span class="num">{with_phone}</span><span class="lbl">with phone</span></div>
    <div class="stat"><span class="num">{with_name}</span><span class="lbl">named human</span></div>
    <div class="stat"><span class="num">{tier_a_b}</span><span class="lbl">A/B tier leads</span></div>
    <div class="stat"><span class="num">{pitch_card_count}</span><span class="lbl">pitch cards</span></div>
  </div>

  <h2>1. Pitch cards (start here)</h2>
  <div class="card">
    <a class="title" href="pitch_cards/index.html">→ Top 50 pitch cards (ranked)</a>
    <p class="meta">Each card: WhatsApp link with pre-filled opener · cold-call script · 3 talking points · objection handler. Clicking a WhatsApp link opens WhatsApp Web/app with the message ready to send.</p>
  </div>

  <h2>2. Full contact list (CSV — open in Excel / Numbers)</h2>
  <div class="card">
    <a class="title" href="cpanel_advisory.csv">→ cpanel_advisory.csv</a>
    <p class="meta">690 sites, with: primary email, all emails, phones, socials, addresses, category, theme. Sort by tranco_rank descending for highest-traffic-first.</p>
  </div>
  <div class="card">
    <a class="title" href="qualified_leads.csv">→ qualified_leads.csv</a>
    <p class="meta">The same 690 sites + the lead-score (0-100), pain signals, SSL days, last-post date. Filter score &gt;= 60 for the priority bucket.</p>
  </div>

  <h2>3. Deeper data (for the engineer in the team)</h2>
  <div class="card">
    <a class="title" href="zwwp.db">→ zwwp.db</a>
    <p class="meta">SQLite database — every contact, every channel, full outreach_history + suppressions tables. Query with <code>sqlite3 zwwp.db</code> or open in DB Browser for SQLite. Key tables: <code>domains</code>, <code>contacts</code>, <code>channels</code>, <code>qualified_leads</code> (view), <code>ssl_expiry</code>, <code>freshness</code>, <code>vulnerabilities</code>.</p>
  </div>

  <h2>How to actually use this</h2>
  <ol>
    <li><strong>Open pitch_cards/index.html</strong> in your browser.</li>
    <li>Pick the top 10–20 by score (the A and B tiers).</li>
    <li>For each: click the <strong>WhatsApp link</strong> in the card. WhatsApp opens with the message pre-filled. Adjust if needed; hit send.</li>
    <li>For the highest-rank sites (Tranco rank set), use the <strong>Cold-call opener</strong> from the card instead.</li>
    <li>When someone replies "stop" / "unsubscribe", add them to suppressions: <code>python scripts/22_suppress.py --add --email &lt;email&gt; --reason replied_stop --source mikey</code> on the source machine.</li>
  </ol>

  <h2>The pitch is</h2>
  <p>cPanel CVE-2026-41940 (pre-auth bypass disclosed early May 2026, actively exploited per Censys observations). Almost every site in the list is on cPanel and therefore in scope. Some additionally have an SSL cert expiring in &lt;30 days, an outdated WordPress core, or no posts in 12+ months. The pitch card for each site picks the most urgent signal for that specific site.</p>

  <h2>Privacy reminders</h2>
  <ul>
    <li>This data was collected from <strong>public</strong> homepages, public RSS feeds, public WP REST API endpoints — same depth a normal visitor sees.</li>
    <li>Even so: <strong>don't post the file URLs anywhere public</strong>, don't share with anyone outside the engagement team, don't bcc 690 emails in one blast.</li>
    <li>If a site owner asks "where did you get my number?", honest answer: "from your public website's contact page".</li>
    <li>If they ask to be removed, suppress them (above), and they won't re-appear in future runs.</li>
  </ul>
</div>
</body></html>
"""


README_TXT = """ZW WP cPanel — engagement kit
==============================

PRIVATE / CONFIDENTIAL. Do not redistribute.

How to use:
1. Unzip this archive somewhere on your Mac / PC.
2. Open OPEN_THIS_FIRST.html in your browser (double-click works).
3. Click "Top 50 pitch cards" to see the ranked lead list.
4. For each lead, the card has a clickable WhatsApp link with a
   pre-filled message — one tap to send.

Contents:
- OPEN_THIS_FIRST.html        index / how-to-use
- pitch_cards/                50 ranked sales-ready cards
- cpanel_advisory.csv         full contact list (690 sites)
- qualified_leads.csv         ranked list with lead_score 0-100
- zwwp.db                     SQLite database for deeper queries
- README.txt                  this file

Privacy:
- Contact data was collected from public web pages.
- Do not upload this zip or its contents to a public site.
- Suppression of unsubscribers happens back on the source machine.

Generated: {generated_at}
Generated by: zw-wp-research pipeline (stage 31)
"""


def export_qualified_csv(conn: sqlite3.Connection, out: Path) -> int:
    ensure_view(conn)
    cur = conn.execute("""
        SELECT
            domain, lead_score, tranco_rank, category, display_name,
            phone, email_hc, email_any, host_panel, mx_provider,
            wp_version, last_post_at, posts_last_90d,
            ssl_days, vuln_count, perf_mobile, first_archived_at,
            suppressed, prior_touches
        FROM qualified_leads
        ORDER BY lead_score DESC, (tranco_rank IS NULL), tranco_rank
    """)
    cols = [c[0] for c in cur.description]
    n = 0
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for row in cur.fetchall():
            w.writerow([row[c] if row[c] is not None else "" for c in cols])
            n += 1
    return n


def collect_stats(conn: sqlite3.Connection) -> dict:
    ensure_view(conn)
    cur = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN email_any IS NOT NULL THEN 1 ELSE 0 END) AS with_email,
            SUM(CASE WHEN phone IS NOT NULL THEN 1 ELSE 0 END) AS with_phone,
            SUM(CASE WHEN display_name IS NOT NULL THEN 1 ELSE 0 END) AS with_name,
            SUM(CASE WHEN lead_score >= 60 THEN 1 ELSE 0 END) AS tier_a_b
        FROM qualified_leads
        WHERE suppressed = 0
    """)
    return dict(cur.fetchone())


def build() -> Path:
    if not PITCH_HTML_DIR.exists():
        print(f"[share] {PITCH_HTML_DIR} missing — run `make pitch-html` first",
              file=sys.stderr)
        sys.exit(1)
    if not ADVISORY_CSV.exists():
        print(f"[share] {ADVISORY_CSV} missing — run `make leadgen-advisory` first",
              file=sys.stderr)
        sys.exit(1)

    # Clean output
    if SHARE_DIR.exists():
        shutil.rmtree(SHARE_DIR)
    SHARE_DIR.mkdir(parents=True)

    # Copy artifacts
    shutil.copytree(PITCH_HTML_DIR, SHARE_DIR / "pitch_cards")
    shutil.copy(ADVISORY_CSV, SHARE_DIR / "cpanel_advisory.csv")
    shutil.copy(DB_PATH, SHARE_DIR / "zwwp.db")

    # Export qualified_leads as CSV
    conn = open_conn(DB_PATH)
    n_qualified = export_qualified_csv(conn, SHARE_DIR / "qualified_leads.csv")
    stats = collect_stats(conn)
    conn.close()

    pitch_count = len(list((SHARE_DIR / "pitch_cards").glob("*.html"))) - 1  # minus index
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    (SHARE_DIR / "OPEN_THIS_FIRST.html").write_text(
        INDEX_HTML.format(
            generated_at=generated_at,
            total=stats.get("total", 0),
            with_email=stats.get("with_email", 0),
            with_phone=stats.get("with_phone", 0),
            with_name=stats.get("with_name", 0),
            tier_a_b=stats.get("tier_a_b", 0),
            pitch_card_count=pitch_count,
        ),
        encoding="utf-8",
    )
    (SHARE_DIR / "README.txt").write_text(
        README_TXT.format(generated_at=generated_at),
        encoding="utf-8",
    )

    # Zip it up
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    zip_path = reports_dir() / f"share-bundle-{date_tag}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in SHARE_DIR.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(SHARE_DIR))

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"[share] bundle written:", file=sys.stderr)
    print(f"    folder: {SHARE_DIR}", file=sys.stderr)
    print(f"    zip:    {zip_path}  ({size_mb:.1f} MB)", file=sys.stderr)
    print(f"    pitch cards: {pitch_count}", file=sys.stderr)
    print(f"    qualified leads exported to CSV: {n_qualified}", file=sys.stderr)
    print(f"    stats: {stats}", file=sys.stderr)
    return zip_path


def main() -> int:
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
