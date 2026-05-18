#!/usr/bin/env python3
"""Stage 15 — DNS-derived contacts.

For every cPanel-positive WordPress domain, query SOA + TXT (apex + _dmarc) +
MX. Decode the SOA admin email, extract DMARC `ruf=mailto:` / `rua=mailto:`,
classify the MX provider. Pure DNS — no HTTP requests against the target.

Emits data/dns_contacts.jsonl AND writes through to the contacts/channels
schema. Idempotent.

Usage:
  python scripts/15_dns_contacts.py
  python scripts/15_dns_contacts.py --scope wp-positive
  python scripts/15_dns_contacts.py --force
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.contacts import add_channel, open_conn, upsert_contact  # noqa: E402
from lib.jsonl import append_record, read_existing_keys  # noqa: E402

try:
    import dns.asyncresolver
    import dns.exception
    import dns.rdatatype
    import dns.resolver
except ImportError:  # pragma: no cover
    dns = None  # type: ignore[assignment]

DB_PATH = reports_dir() / "zwwp.db"
DNS_PATH = data_dir() / "dns_contacts.jsonl"


# Common MX → provider mapping. Add new ones over time; never guess.
MX_PROVIDERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:^|\.)aspmx\.l\.google\.com\.?$", re.I), "google"),
    (re.compile(r"(?:^|\.)googlemail\.com\.?$", re.I), "google"),
    (re.compile(r"(?:^|\.)mail\.protection\.outlook\.com\.?$", re.I), "microsoft365"),
    (re.compile(r"(?:^|\.)protection\.outlook\.com\.?$", re.I), "microsoft365"),
    (re.compile(r"(?:^|\.)zoho\.com\.?$", re.I), "zoho"),
    (re.compile(r"(?:^|\.)zoho\.eu\.?$", re.I), "zoho"),
    (re.compile(r"(?:^|\.)mailgun\.org\.?$", re.I), "mailgun"),
    (re.compile(r"(?:^|\.)sendgrid\.net\.?$", re.I), "sendgrid"),
    (re.compile(r"(?:^|\.)mxroute\.(?:com|net)\.?$", re.I), "mxroute"),
    (re.compile(r"(?:^|\.)cloudflare\.net\.?$", re.I), "cloudflare-routing"),
    (re.compile(r"(?:^|\.)protonmail\.ch\.?$", re.I), "protonmail"),
    (re.compile(r"(?:^|\.)fastmail\.com\.?$", re.I), "fastmail"),
    (re.compile(r"(?:^|\.)yandex\.net\.?$", re.I), "yandex"),
    (re.compile(r"(?:^|\.)mxlogic\.net\.?$", re.I), "mxlogic"),
    (re.compile(r"(?:^|\.)titan\.email\.?$", re.I), "titan"),
    (re.compile(r"(?:^|\.)mail\.hostinger\.com\.?$", re.I), "hostinger-mail"),
]


def classify_mx(mx_target: str, *, domain: str, a_record_ip: str | None) -> str:
    if not mx_target:
        return "none"
    for pat, name in MX_PROVIDERS:
        if pat.search(mx_target):
            return name
    # Self-hosted heuristic: MX target's apex == the domain itself
    target = mx_target.rstrip(".").lower()
    domain = domain.lower()
    if target == domain or target.endswith("." + domain):
        return "self-hosted"
    return f"other:{target}"


# DMARC parsing: extract any mailto: addresses from ruf= and rua= tags.
_DMARC_MAILTO_RE = re.compile(r"\b(ruf|rua)=([^;]+)", re.I)
_MAILTO_RE = re.compile(r"mailto:([^,\s;]+)", re.I)


def parse_dmarc_addresses(txt_values: list[str]) -> tuple[list[str], list[str]]:
    """Return (rua_emails, ruf_emails)."""
    rua: list[str] = []
    ruf: list[str] = []
    for txt in txt_values:
        if "v=dmarc1" not in txt.lower():
            continue
        for m in _DMARC_MAILTO_RE.finditer(txt):
            kind = m.group(1).lower()
            for addr_m in _MAILTO_RE.finditer(m.group(2)):
                email = addr_m.group(1).strip().lower()
                if kind == "rua" and email not in rua:
                    rua.append(email)
                elif kind == "ruf" and email not in ruf:
                    ruf.append(email)
    return rua, ruf


def decode_soa_rname(rname_text: str) -> str | None:
    """Decode the SOA RNAME format (admin.example.com.) into admin@example.com.

    Per RFC 1035 §3.3.13, the first non-escaped dot separates the local part
    from the domain. Escaped dots (\\.) are literal dots inside the local part.
    """
    if not rname_text:
        return None
    s = rname_text.rstrip(".")
    if not s or "." not in s:
        return None
    # Walk characters; treat '\.' as literal, the first un-escaped '.' is the split.
    local_chars: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] == ".":
            local_chars.append(".")
            i += 2
            continue
        if c == ".":
            domain_part = s[i + 1:]
            local = "".join(local_chars)
            if not local or not domain_part:
                return None
            return f"{local}@{domain_part}"
        local_chars.append(c)
        i += 1
    return None


# ---- async resolver ---------------------------------------------------
_RESOLVER: "dns.asyncresolver.Resolver | None" = None


def _resolver() -> "dns.asyncresolver.Resolver":
    global _RESOLVER
    if dns is None:
        raise RuntimeError("dnspython is not installed")
    if _RESOLVER is None:
        r = dns.asyncresolver.Resolver(configure=False)
        r.nameservers = ["1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4"]
        r.timeout = 3.0
        r.lifetime = 4.0
        _RESOLVER = r
    return _RESOLVER


async def _query(name: str, rdtype: str) -> list:
    try:
        ans = await _resolver().resolve(name, rdtype)
        return list(ans)
    except (
        dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers,
        dns.exception.Timeout, dns.exception.DNSException,
    ):
        return []


async def gather_dns(domain: str, *, ip: str | None) -> dict:
    """Resolve SOA + TXT@apex + TXT@_dmarc + MX. Returns a normalised dict."""
    soa_admin_email: str | None = None
    soa_records = await _query(domain, "SOA")
    if soa_records:
        rname = soa_records[0].rname.to_text()
        soa_admin_email = decode_soa_rname(rname)

    txt_apex = [
        rr.strings[0].decode("ascii", errors="replace") if rr.strings else ""
        for rr in await _query(domain, "TXT")
    ]
    txt_dmarc = [
        rr.strings[0].decode("ascii", errors="replace") if rr.strings else ""
        for rr in await _query(f"_dmarc.{domain}", "TXT")
    ]
    # Some big TXTs are chunked — concatenate per-record strings.
    def _flatten(rrs):
        out: list[str] = []
        for rr in rrs:
            try:
                out.append(b"".join(rr.strings).decode("ascii", errors="replace"))
            except Exception:
                pass
        return out
    flat_apex = _flatten(await _query(domain, "TXT"))
    flat_dmarc = _flatten(await _query(f"_dmarc.{domain}", "TXT"))
    rua, ruf = parse_dmarc_addresses(flat_apex + flat_dmarc)

    mx_records = await _query(domain, "MX")
    mx_records.sort(key=lambda r: getattr(r, "preference", 999))
    mx_primary = ""
    mx_provider = "none"
    if mx_records:
        mx_primary = str(mx_records[0].exchange).rstrip(".")
        mx_provider = classify_mx(mx_primary, domain=domain, a_record_ip=ip)

    return {
        "domain": domain,
        "soa_admin_email": soa_admin_email,
        "dmarc_rua": rua,
        "dmarc_ruf": ruf,
        "mx_primary": mx_primary,
        "mx_provider": mx_provider,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---- DB write-through -------------------------------------------------
def _looks_like_email(s: str | None) -> bool:
    return bool(s and "@" in s and "." in s.split("@", 1)[-1])


def write_to_schema(conn, rec: dict) -> None:
    domain = rec["domain"]
    soa = rec.get("soa_admin_email")
    if _looks_like_email(soa):
        cid = upsert_contact(
            conn, domain=domain, source="dns_soa", confidence=0.4,
            role="hostmaster",
        )
        add_channel(
            conn, contact_id=cid, kind="email", value=soa.lower(),
            source="dns_soa", confidence=0.4,
        )
    for addr in rec.get("dmarc_rua") or []:
        if not _looks_like_email(addr):
            continue
        cid = upsert_contact(
            conn, domain=domain, source="dns_dmarc_rua", confidence=0.5,
            role="dmarc-rua",
        )
        add_channel(
            conn, contact_id=cid, kind="email", value=addr.lower(),
            source="dns_dmarc_rua", confidence=0.5,
        )
    for addr in rec.get("dmarc_ruf") or []:
        if not _looks_like_email(addr):
            continue
        cid = upsert_contact(
            conn, domain=domain, source="dns_dmarc_ruf", confidence=0.5,
            role="dmarc-ruf",
        )
        add_channel(
            conn, contact_id=cid, kind="email", value=addr.lower(),
            source="dns_dmarc_ruf", confidence=0.5,
        )
    # MX provider lands on the `domains` table — that's where downstream
    # agents expect routing hints. Add the column if missing.
    conn.execute(
        "UPDATE domains SET mx_provider = ? WHERE domain = ?",
        (rec.get("mx_provider"), domain),
    )


def ensure_mx_column(conn) -> None:
    """Add `mx_provider TEXT` to `domains` if it's not already there."""
    cur = conn.execute("PRAGMA table_info(domains)")
    if not any(r["name"] == "mx_provider" for r in cur.fetchall()):
        conn.execute("ALTER TABLE domains ADD COLUMN mx_provider TEXT")
        conn.commit()


# ---- main orchestration ----------------------------------------------
SCOPES = {
    "cpanel-wp": "host_panel='cpanel' AND score>=70",
    "wp-positive": "score>=70",
    "all": "1=1",
}


async def run(scope: str, force: bool, concurrency: int, limit: int | None) -> int:
    if dns is None:
        print("dnspython missing — install with `pip install dnspython`", file=sys.stderr)
        return 0
    conn = open_conn(DB_PATH)
    ensure_mx_column(conn)
    where = SCOPES.get(scope, SCOPES["cpanel-wp"])
    rows = conn.execute(
        f"SELECT domain, ip FROM domains WHERE {where} ORDER BY (tranco_rank IS NULL), tranco_rank"
    ).fetchall()
    domains = [(r["domain"], r["ip"]) for r in rows]
    if limit:
        domains = domains[:limit]
    seen = set() if force else read_existing_keys(DNS_PATH, "domain")
    pending = [(d, ip) for d, ip in domains if d not in seen]
    print(f"[dns] scope={scope}  total={len(domains)}  pending={len(pending)}",
          file=sys.stderr)
    sem = asyncio.Semaphore(concurrency)
    written = 0

    async def worker(domain: str, ip: str | None) -> None:
        nonlocal written
        async with sem:
            try:
                rec = await gather_dns(domain, ip=ip)
            except Exception as exc:
                rec = {
                    "domain": domain, "error": str(exc)[:160],
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
        append_record(DNS_PATH, rec)
        if "error" not in rec:
            try:
                write_to_schema(conn, rec)
            except Exception as exc:
                print(f"[dns] write-through failed for {domain}: {exc}",
                      file=sys.stderr)
        written += 1
        if written % 100 == 0:
            conn.commit()
            print(f"[dns] {written}/{len(pending)} done", file=sys.stderr)

    await asyncio.gather(*(worker(d, ip) for d, ip in pending))
    conn.commit()
    conn.close()
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=list(SCOPES.keys()), default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--concurrency", type=int, default=200)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    n = asyncio.run(run(args.scope, args.force, args.concurrency, args.limit))
    print(f"[dns] wrote {n} records to {DNS_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
