#!/usr/bin/env python3
"""Stage 24 — SSL cert expiry (TLS re-probe).

For every cPanel WordPress site, open a TLS connection, extract the peer
certificate's `notBefore` / `notAfter` fields + the issuer CN. Compute
days-remaining. Sites with <30 days are sales-actionable ("your SSL
expires next week — want me to fix it?").

Writes to `ssl_expiry` table. Idempotent.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.x509.oid import NameOID
except ImportError:
    x509 = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import data_dir, reports_dir  # noqa: E402
from lib.contacts import open_conn  # noqa: E402
from lib.jsonl import append_record, read_existing_keys  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
SSL_PATH = data_dir() / "ssl_expiry.jsonl"

SCOPES = {
    "cpanel-wp": "host_panel='cpanel' AND score>=70",
    "wp-positive": "score>=70",
    "all": "1=1",
}


def _parse_x509_time(s: str) -> datetime | None:
    """Parse the X509 time format used by Python ssl: 'May 18 23:59:59 2026 GMT'."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def probe_cert(host: str, port: int = 443, timeout: float = 6.0) -> dict | None:
    """Sync TLS handshake, return cert metadata or None on failure.

    Uses `getpeercert(binary_form=True)` because `verify_mode=CERT_NONE`
    makes Python return an empty dict on the dict-form call — but the raw
    DER bytes are always available. Parse with `cryptography`.
    """
    if x509 is None:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                der = ssock.getpeercert(binary_form=True)
                if not der:
                    return None
                cert = x509.load_der_x509_certificate(der, default_backend())
                # Python 3.12+ deprecates not_valid_before in favour of *_utc
                try:
                    not_before = cert.not_valid_before_utc
                except AttributeError:
                    not_before = cert.not_valid_before.replace(tzinfo=timezone.utc)
                try:
                    not_after = cert.not_valid_after_utc
                except AttributeError:
                    not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
                issuer_cn = None
                try:
                    cn_attrs = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
                    if cn_attrs:
                        issuer_cn = cn_attrs[0].value
                except Exception:
                    pass
                days_remaining = max(
                    -3650,
                    int((not_after - datetime.now(timezone.utc)).total_seconds() // 86400),
                )
                return {
                    "not_before": not_before.isoformat(),
                    "not_after": not_after.isoformat(),
                    "days_remaining": days_remaining,
                    "issuer_cn": issuer_cn,
                }
    except Exception:
        return None


async def aprobe(host: str) -> dict | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, probe_cert, host)


def write_to_schema(conn, domain: str, rec: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO ssl_expiry
            (domain, not_before, not_after, days_remaining, issuer_cn, fetched_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (domain, rec.get("not_before"), rec.get("not_after"),
         rec.get("days_remaining"), rec.get("issuer_cn")),
    )


async def run(scope: str, force: bool, concurrency: int, limit: int | None) -> int:
    conn = open_conn(DB_PATH)
    where = SCOPES.get(scope, SCOPES["cpanel-wp"])
    rows = conn.execute(
        f"SELECT domain FROM domains WHERE {where} "
        "ORDER BY (tranco_rank IS NULL), tranco_rank"
    ).fetchall()
    domains = [r["domain"] for r in rows]
    if limit:
        domains = domains[:limit]
    seen = set() if force else read_existing_keys(SSL_PATH, "domain")
    pending = [d for d in domains if d not in seen]
    print(f"[ssl] scope={scope}  total={len(domains)}  pending={len(pending)}",
          file=sys.stderr)
    if not pending:
        return 0
    sem = asyncio.Semaphore(concurrency)
    written = 0
    have_cert = 0
    expiring = 0
    expired = 0

    async def worker(domain: str) -> None:
        nonlocal written, have_cert, expiring, expired
        async with sem:
            try:
                cert = await asyncio.wait_for(aprobe(domain), timeout=10)
            except Exception:
                cert = None
        rec = {
            "domain": domain,
            **(cert or {}),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        append_record(SSL_PATH, rec)
        if cert:
            write_to_schema(conn, domain, cert)
            have_cert += 1
            dr = cert.get("days_remaining") or 99999
            if dr < 0:
                expired += 1
            elif dr < 30:
                expiring += 1
        written += 1
        if written % 50 == 0:
            conn.commit()
            print(f"[ssl] {written}/{len(pending)} have_cert={have_cert} "
                  f"expiring<30d={expiring} expired={expired}",
                  file=sys.stderr)

    await asyncio.gather(*(worker(d) for d in pending))
    conn.commit()
    conn.close()
    print(f"[ssl] FINAL have_cert={have_cert} expiring<30d={expiring} expired={expired}",
          file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=list(SCOPES.keys()), default="cpanel-wp")
    p.add_argument("--force", action="store_true")
    p.add_argument("--concurrency", type=int, default=40)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    n = asyncio.run(run(args.scope, args.force, args.concurrency, args.limit))
    print(f"[ssl] wrote {n} records to {SSL_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
