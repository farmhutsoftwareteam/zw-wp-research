#!/usr/bin/env python3
"""Stage 16 — WordPress REST API author harvester.

For each cPanel WordPress site, query the documented public endpoints that
expose author display names and (sometimes) emails:

  1. GET /wp-json/wp/v2/users?per_page=100&context=embed  — public author list
  2. GET /wp-json/wp/v2/users?per_page=100                 — same w/o embed
  3. GET /feed/                                            — RSS dc:creator + author
  4. GET /wp-json/oembed/1.0/embed?url=https://<d>/        — author_name + author_url

Only `context=embed` is requested by default — that's the safe public subset.
The ?author=N email-leak probe is gated behind --include-author-id-leak (off).

Writes through to the contacts/channels schema. Idempotent.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.contacts import add_channel, open_conn, upsert_contact  # noqa: E402
from lib.http import polite_client, PoliteClient  # noqa: E402
from lib.jsonl import append_record, read_existing_keys  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
WP_AUTHORS_PATH = data_dir() / "wp_authors.jsonl"

# RSS namespace constants — `xml.etree.ElementTree` needs the URI form.
NS_DC = "{http://purl.org/dc/elements/1.1/}"
NS_ATOM = "{http://www.w3.org/2005/Atom}"


SCOPES = {
    "cpanel-wp": "host_panel='cpanel' AND score>=70",
    "wp-positive": "score>=70",
    "all": "1=1",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _emails_in(text: str) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(m.lower() for m in _EMAIL_RE.findall(text)))


# ---- parsers ----------------------------------------------------------
def parse_wp_users(payload) -> list[dict]:
    """Normalise wp-json/wp/v2/users response. Tolerates partial fields."""
    if not isinstance(payload, list):
        return []
    out: list[dict] = []
    seen_slugs: set[str] = set()
    for u in payload:
        if not isinstance(u, dict):
            continue
        slug = (u.get("slug") or "").strip()
        if slug and slug in seen_slugs:
            continue
        if slug:
            seen_slugs.add(slug)
        record = {
            "id": u.get("id"),
            "slug": slug or None,
            "name": (u.get("name") or "").strip() or None,
            "link": (u.get("link") or "").strip() or None,
            "description": (u.get("description") or "").strip() or None,
        }
        # email is only present on view/edit contexts — collect if present
        if u.get("email"):
            record["email"] = u["email"].strip().lower()
        # description may itself contain an email
        emails = _emails_in(record.get("description") or "")
        if emails:
            record["emails_in_description"] = emails
        out.append(record)
    return out


def parse_feed_authors(xml_text: str) -> list[dict]:
    """Extract author/dc:creator entries from a WP RSS or Atom feed."""
    out: list[dict] = []
    if not xml_text:
        return out
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    seen: set[str] = set()
    # RSS 2.0 channel/item author/dc:creator
    for item in root.iter():
        tag = item.tag
        if tag.endswith("}creator") or tag == "creator":
            name = (item.text or "").strip()
            if name and name not in seen:
                seen.add(name)
                out.append({"name": name, "source": "feed_dc_creator"})
        elif tag.endswith("}author") or tag == "author":
            text = (item.text or "").strip()
            # RSS 2.0 <author> form: "email (Name)"
            email = None
            name = None
            m = re.match(r"\s*([^\s(]+@[^\s)]+)\s*\(([^)]+)\)\s*$", text)
            if m:
                email = m.group(1).lower()
                name = m.group(2).strip()
            else:
                emails = _emails_in(text)
                if emails:
                    email = emails[0]
                else:
                    name = text or None
            if email or name:
                key = (name or "") + "|" + (email or "")
                if key not in seen:
                    seen.add(key)
                    out.append({"name": name, "email": email, "source": "feed_author"})
    return out


# ---- HTTP probes ------------------------------------------------------
async def fetch_wp_users(client: PoliteClient, base: str, *, with_embed: bool) -> tuple[list[dict], int | None]:
    suffix = "/wp-json/wp/v2/users?per_page=100"
    if with_embed:
        suffix += "&context=embed"
    url = base + suffix
    try:
        resp = await client.get(url)
    except Exception:
        return [], None
    if resp is None:
        return [], None
    status = resp.status_code
    if status != 200:
        return [], status
    try:
        payload = resp.json()
    except Exception:
        return [], status
    return parse_wp_users(payload), status


async def fetch_feed(client: PoliteClient, base: str) -> list[dict]:
    try:
        resp = await client.get(base + "/feed/")
    except Exception:
        return []
    if resp is None or resp.status_code != 200 or not resp.text:
        return []
    return parse_feed_authors(resp.text)


async def fetch_oembed(client: PoliteClient, base: str, domain: str) -> dict | None:
    """oEmbed endpoint sometimes returns author_name / author_url for the homepage."""
    url = base + "/wp-json/oembed/1.0/embed?url=" + base + "/"
    try:
        resp = await client.get(url)
    except Exception:
        return None
    if resp is None or resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    name = (payload.get("author_name") or "").strip()
    link = (payload.get("author_url") or "").strip()
    if not (name or link):
        return None
    return {"name": name or None, "link": link or None, "source": "oembed"}


# ---- per-domain orchestration ----------------------------------------
async def harvest_one(client: PoliteClient, domain: str, scheme: str) -> dict:
    base = f"{scheme}://{domain}"
    authors: list[dict] = []
    emails_seen: list[str] = []

    users, status_embed = await fetch_wp_users(client, base, with_embed=True)
    status_plain: int | None = None
    if not users:
        users, status_plain = await fetch_wp_users(client, base, with_embed=False)
    for u in users:
        authors.append({**u, "source": "wp_users"})
        if u.get("email"):
            emails_seen.append(u["email"])
        for e in u.get("emails_in_description") or []:
            emails_seen.append(e)

    feed = await fetch_feed(client, base)
    authors.extend(feed)
    for a in feed:
        if a.get("email"):
            emails_seen.append(a["email"])

    oembed = await fetch_oembed(client, base, domain)
    if oembed:
        authors.append(oembed)

    emails_seen = list(dict.fromkeys(emails_seen))
    return {
        "domain": domain,
        "wp_users_status_embed": status_embed,
        "wp_users_status_plain": status_plain,
        "authors": authors,
        "emails_seen": emails_seen,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---- DB write-through -------------------------------------------------
def write_to_schema(conn, rec: dict) -> dict[str, int]:
    counts = {"contacts": 0, "names": 0, "emails": 0, "websites": 0}
    domain = rec["domain"]
    for a in rec.get("authors") or []:
        name = a.get("name")
        source = a.get("source", "wp_users")
        confidence = 0.7 if source == "wp_users" else (0.6 if source == "oembed" else 0.55)
        cid = upsert_contact(
            conn,
            domain=domain,
            display_name=name,
            role="wp_author",
            source=source,
            confidence=confidence,
        )
        counts["contacts"] += 1
        if name:
            counts["names"] += 1
        link = a.get("link")
        if link:
            if add_channel(
                conn, contact_id=cid, kind="website", value=link,
                source=source, confidence=0.6,
            ) is not None:
                counts["websites"] += 1
        email = a.get("email")
        if email and "@" in email:
            if add_channel(
                conn, contact_id=cid, kind="email", value=email.lower(),
                source=source, confidence=0.8,
            ) is not None:
                counts["emails"] += 1
        for e in a.get("emails_in_description") or []:
            if "@" in e and add_channel(
                conn, contact_id=cid, kind="email", value=e.lower(),
                source="wp_users_description", confidence=0.7,
            ) is not None:
                counts["emails"] += 1
    return counts


async def run(scope: str, force: bool, concurrency: int, limit: int | None,
              include_author_id_leak: bool) -> int:
    cfg = Config.from_env()
    conn = open_conn(DB_PATH)
    where = SCOPES.get(scope, SCOPES["cpanel-wp"])
    rows = conn.execute(
        f"""SELECT domain, scheme_used FROM domains
            WHERE {where}
            ORDER BY (tranco_rank IS NULL), tranco_rank"""
    ).fetchall()
    if limit:
        rows = rows[:limit]
    seen = set() if force else read_existing_keys(WP_AUTHORS_PATH, "domain")
    pending = [(r["domain"], r["scheme_used"] or "https") for r in rows if r["domain"] not in seen]
    print(f"[wp] scope={scope}  total={len(rows)}  pending={len(pending)}",
          file=sys.stderr)
    if include_author_id_leak:
        print("[wp] WARNING: --include-author-id-leak is on (not used in this build)",
              file=sys.stderr)
    sem = asyncio.Semaphore(concurrency)
    written = 0
    total_counts = {"contacts": 0, "names": 0, "emails": 0, "websites": 0}

    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=cfg.rps_per_host,
        timeout=10,
        max_concurrent=200,
    ) as client:

        async def worker(domain: str, scheme: str) -> None:
            nonlocal written
            async with sem:
                try:
                    rec = await harvest_one(client, domain, scheme)
                except Exception as exc:
                    rec = {
                        "domain": domain, "error": str(exc)[:160],
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
            append_record(WP_AUTHORS_PATH, rec)
            if "error" not in rec:
                c = write_to_schema(conn, rec)
                for k in total_counts:
                    total_counts[k] += c.get(k, 0)
            written += 1
            if written % 50 == 0:
                conn.commit()
                print(f"[wp] {written}/{len(pending)} done  totals={total_counts}",
                      file=sys.stderr)

        await asyncio.gather(*(worker(d, s) for d, s in pending))
    conn.commit()
    conn.close()
    print(f"[wp] final totals: {total_counts}", file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=list(SCOPES.keys()), default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--concurrency", type=int, default=50)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--include-author-id-leak", action="store_true",
                   help="(reserved) — not implemented yet; flag present for compat")
    args = p.parse_args()
    n = asyncio.run(run(args.scope, args.force, args.concurrency,
                       args.limit, args.include_author_id_leak))
    print(f"[wp] wrote {n} records to {WP_AUTHORS_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
