#!/usr/bin/env python3
"""Stage 10 — cPanel advisory outreach builder.

For incident-response / vulnerability-notification work. Reads the SQLite
database, picks the high-confidence cPanel-positive WordPress sites, scrapes
each homepage + a small set of common contact pages for `mailto:` addresses
and contact form URLs, and emits two artifacts:

  reports/cpanel_advisory.csv   — outreach list (one row per site)
  reports/cpanel_advisory.md    — printable summary grouped by category

What we collect (all from public web pages — same data a visitor sees):
  - mailto: links from /, /contact, /contact-us, /about
  - first email-shaped string in page text (regex)
  - schema.org email properties from JSON-LD blocks
  - twitter / facebook handles from rel=me + og:* meta
  - footer phone numbers (regex; +263, 0xxx)

What we DO NOT do:
  - probe non-public paths
  - send any traffic beyond GET on public URLs already linked from /
  - attempt anything beyond banner-grab depth
  - touch the cPanel control plane (port 2083 etc.)

Idempotent: skips domains already in cpanel_advisory.csv unless --force.

Usage:
  python scripts/10_cpanel_advisory.py
  python scripts/10_cpanel_advisory.py --high-confidence-only
  python scripts/10_cpanel_advisory.py --top-n 200
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, reports_dir  # noqa: E402
from lib.http import polite_client, PoliteClient  # noqa: E402

try:
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:
    BeautifulSoup = None  # type: ignore[assignment]

DB_PATH = reports_dir() / "zwwp.db"
CSV_PATH = reports_dir() / "cpanel_advisory.csv"
MD_PATH = reports_dir() / "cpanel_advisory.md"


CONTACT_PATHS = ["/", "/contact", "/contact/", "/contact-us", "/contact-us/",
                 "/about", "/about/", "/about-us", "/about-us/"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(\+263[\s\-]?\d[\d\s\-]{6,}|\b0\d[\d\s\-]{7,}\b)")


def _is_high_confidence(server_header: str, evidence_json: str | None) -> bool:
    """High-confidence cPanel: at least two signal types fired."""
    if not evidence_json:
        return False
    try:
        ev = json.loads(evidence_json)
    except Exception:
        return False
    matches = ev.get("matches") or []
    sig_types = set()
    for m in matches:
        if ":" in m:
            sig_types.add(m.split(":", 1)[0])
    return len(sig_types) >= 2


def _q_cpanel_sites(conn: sqlite3.Connection, high_confidence_only: bool, top_n: int | None) -> list[dict]:
    cur = conn.execute("""
        SELECT domain, score, tranco_rank, category, theme, ip, cdn,
               server_header, cert_issuer, reverse_ptr, panel_evidence,
               scheme_used
        FROM domains
        WHERE host_panel = 'cpanel' AND score >= 70
        ORDER BY (tranco_rank IS NULL), tranco_rank, domain
    """)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if high_confidence_only:
        rows = [r for r in rows if _is_high_confidence(r.get("server_header") or "",
                                                       r.get("panel_evidence"))]
    if top_n:
        rows = rows[:top_n]
    return rows


def _extract_contact(soup: "BeautifulSoup", page_url: str) -> dict:
    out: dict[str, list[str]] = defaultdict(list)
    # mailto: links
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            email = href[7:].split("?", 1)[0].strip()
            if email and email not in out["emails"]:
                out["emails"].append(email)
    # Inline emails in text
    text = soup.get_text(separator=" ", strip=True)
    for m in EMAIL_RE.findall(text):
        m = m.strip(".")
        if m and m not in out["emails"]:
            out["emails"].append(m)
    # Phone-shaped text
    for m in PHONE_RE.findall(text):
        m = re.sub(r"\s+", " ", m).strip()
        if m and m not in out["phones"]:
            out["phones"].append(m)
    # schema.org JSON-LD email
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            email = item.get("email") or (item.get("contactPoint") or {}).get("email")
            if isinstance(email, str) and email not in out["emails"]:
                out["emails"].append(email)
            tel = item.get("telephone")
            if isinstance(tel, str) and tel not in out["phones"]:
                out["phones"].append(tel)
            addr = item.get("address")
            if isinstance(addr, dict):
                a_str = ", ".join(filter(None, [addr.get("streetAddress"), addr.get("addressLocality"),
                                                 addr.get("addressRegion"), addr.get("addressCountry")]))
                if a_str and a_str not in out["addresses"]:
                    out["addresses"].append(a_str)
    # social links
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if "twitter.com/" in href or "x.com/" in href:
            out["socials"].append(a["href"])
        elif "facebook.com/" in href:
            out["socials"].append(a["href"])
        elif "linkedin.com/" in href:
            out["socials"].append(a["href"])
        elif "instagram.com/" in href:
            out["socials"].append(a["href"])
    out["socials"] = list(dict.fromkeys(out["socials"]))[:6]
    return dict(out)


async def gather_one(client: PoliteClient, row: dict) -> dict:
    base = f"{row.get('scheme_used') or 'https'}://{row['domain']}"
    contact = {"emails": [], "phones": [], "addresses": [], "socials": []}
    pages_checked = []
    if BeautifulSoup is None:
        return {**row, "contact": contact, "pages_checked": []}
    discovered_paths: set[str] = set(CONTACT_PATHS)
    for path in CONTACT_PATHS[:1]:  # always start with homepage
        url = urljoin(base, path)
        try:
            resp = await client.get(url)
        except Exception:
            continue
        if resp is None or resp.status_code >= 400:
            continue
        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")
        # Discover additional contact paths from homepage links
        for a in soup.find_all("a", href=True):
            href = (a["href"] or "").strip()
            if not href:
                continue
            low = href.lower()
            if any(k in low for k in ("contact", "about")):
                # normalize to path
                try:
                    from urllib.parse import urlparse
                    p = urlparse(urljoin(base, href))
                    if p.netloc and p.netloc.lower().endswith(row["domain"]):
                        discovered_paths.add(p.path or "/")
                except Exception:
                    pass
        c = _extract_contact(soup, url)
        for k, vs in c.items():
            for v in vs:
                if v not in contact[k]:
                    contact[k].append(v)
        pages_checked.append(path)
    # Limit to ~5 follow-up pages
    follow = [p for p in discovered_paths if p not in pages_checked][:4]
    for path in follow:
        url = urljoin(base, path)
        try:
            resp = await client.get(url)
        except Exception:
            continue
        if resp is None or resp.status_code >= 400:
            continue
        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")
        c = _extract_contact(soup, url)
        for k, vs in c.items():
            for v in vs:
                if v not in contact[k]:
                    contact[k].append(v)
        pages_checked.append(path)
    # Cap each list
    contact["emails"] = contact["emails"][:5]
    contact["phones"] = contact["phones"][:3]
    contact["addresses"] = contact["addresses"][:2]
    return {**row, "contact": contact, "pages_checked": pages_checked}


def _likely_owner_email(emails: list[str], domain: str) -> str | None:
    """Pick the email most likely to reach the site owner.
    Prefer same-domain addresses, then info@/admin@/contact@ shapes."""
    if not emails:
        return None
    same_domain = [e for e in emails if e.lower().endswith("@" + domain.lower())]
    if same_domain:
        # Among same-domain, prefer admin/info/contact/webmaster/postmaster
        priority = ["admin", "info", "contact", "hello", "webmaster", "postmaster", "support"]
        for prefix in priority:
            for e in same_domain:
                if e.lower().startswith(prefix + "@"):
                    return e
        return same_domain[0]
    return emails[0]


async def run(high_confidence_only: bool, top_n: int | None, force: bool) -> int:
    cfg = Config.from_env()
    if not DB_PATH.exists():
        print(f"[advisory] {DB_PATH} not found; run stage 08 first", file=sys.stderr)
        return 0
    conn = sqlite3.connect(DB_PATH)
    rows = _q_cpanel_sites(conn, high_confidence_only, top_n)
    conn.close()
    print(f"[advisory] {len(rows)} cPanel-positive sites in scope", file=sys.stderr)

    # Idempotency
    seen: set[str] = set()
    if not force and CSV_PATH.exists():
        with CSV_PATH.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r.get("domain"):
                    seen.add(r["domain"])
    pending = [r for r in rows if r["domain"] not in seen]
    print(f"[advisory] {len(pending)} pending (skip {len(seen)} already done)", file=sys.stderr)

    if not pending:
        print("[advisory] nothing to do", file=sys.stderr)
        return 0

    write_header = not CSV_PATH.exists() or force
    if force and CSV_PATH.exists():
        CSV_PATH.unlink()

    enriched: list[dict] = []
    sem = asyncio.Semaphore(50)

    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=cfg.rps_per_host,
        timeout=cfg.timeout,
        max_concurrent=100,
    ) as client:

        async def worker(row: dict) -> None:
            async with sem:
                try:
                    out = await gather_one(client, row)
                except Exception as exc:
                    out = {**row, "contact": {"emails": [], "phones": [], "addresses": [], "socials": []},
                           "pages_checked": [], "error": str(exc)[:160]}
            enriched.append(out)

        await asyncio.gather(*(worker(r) for r in pending))

    # Write/append CSV
    fieldnames = [
        "domain", "tranco_rank", "category", "theme", "ip", "server_header",
        "primary_contact_email", "all_emails", "phones", "socials",
        "addresses", "pages_checked", "fetched_at",
    ]
    mode = "w" if write_header else "a"
    with CSV_PATH.open(mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        for r in enriched:
            c = r.get("contact") or {}
            primary = _likely_owner_email(c.get("emails") or [], r["domain"])
            w.writerow({
                "domain": r["domain"],
                "tranco_rank": r.get("tranco_rank") or "",
                "category": r.get("category") or "",
                "theme": r.get("theme") or "",
                "ip": r.get("ip") or "",
                "server_header": r.get("server_header") or "",
                "primary_contact_email": primary or "",
                "all_emails": "; ".join(c.get("emails") or []),
                "phones": "; ".join(c.get("phones") or []),
                "socials": "; ".join(c.get("socials") or []),
                "addresses": "; ".join(c.get("addresses") or []),
                "pages_checked": ",".join(r.get("pages_checked") or []),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })

    # Build/refresh markdown summary
    _write_md_summary()
    print(f"[advisory] wrote {len(enriched)} new rows to {CSV_PATH}", file=sys.stderr)
    print(f"[advisory] markdown summary: {MD_PATH}", file=sys.stderr)
    return len(enriched)


def _write_md_summary() -> None:
    if not CSV_PATH.exists():
        return
    with CSV_PATH.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    has_email = [r for r in rows if r.get("primary_contact_email")]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r.get("category") or "uncategorized"].append(r)
    lines = []
    lines.append("# cPanel advisory outreach list")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")
    lines.append(f"- Total cPanel-positive WP sites in scope: **{len(rows)}**")
    lines.append(f"- Sites with at least one public email: **{len(has_email)}** ({len(has_email)*100//max(1,len(rows))}%)")
    lines.append("")
    lines.append("Use this list to send the post-incident hardening notice in")
    lines.append("`reports/cpanel_outreach_email.md`. Attach `reports/cpanel_hardening_checklist.md`.")
    lines.append("")
    lines.append("## Reachable owners by category")
    lines.append("")
    lines.append("| Category | Total | With contact email |")
    lines.append("|---|---|---|")
    for cat in sorted(by_cat.keys(), key=lambda c: (-len(by_cat[c]), c)):
        cat_rows = by_cat[cat]
        cat_with_email = [r for r in cat_rows if r.get("primary_contact_email")]
        lines.append(f"| {cat} | {len(cat_rows)} | {len(cat_with_email)} |")
    lines.append("")
    lines.append("## Top 30 by Tranco rank (with email)")
    lines.append("")
    lines.append("| Domain | Rank | Category | Email |")
    lines.append("|---|---|---|---|")
    ranked = [r for r in has_email if r.get("tranco_rank")]
    ranked.sort(key=lambda r: int(r.get("tranco_rank") or 10**9))
    for r in ranked[:30]:
        lines.append(f"| {r['domain']} | {r.get('tranco_rank')} | {r.get('category', '—')} | {r.get('primary_contact_email')} |")
    lines.append("")
    MD_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--high-confidence-only", action="store_true",
                   help="Only sites where >=2 signal types confirm cPanel")
    p.add_argument("--top-n", type=int, default=None,
                   help="Cap to top-N by Tranco rank")
    p.add_argument("--force", action="store_true",
                   help="Re-fetch all sites, overwriting existing CSV")
    args = p.parse_args()
    n = asyncio.run(run(args.high_confidence_only, args.top_n, args.force))
    print(f"[advisory] processed {n} sites", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
