#!/usr/bin/env python3
"""Stage 03b — Hosting control-panel fingerprinter.

For every domain in data/detections.jsonl, gather six independent signals and
emit a single panel verdict (cpanel | plesk | directadmin | hestia | vesta |
webmin | litespeed | None) to data/panels.jsonl.

Signals:
  1. HTTP `Server:` header (one extra GET / on the homepage)
  2. HTML body markers
  3. TLS cert issuer (synchronous TLS handshake)
  4. Reverse DNS PTR of the resolved A-record IP
  5. Path probes — /.well-known/cpanel-dcv/, /cgi-sys/defaultwebpage.cgi,
     /plesk-stat/login.php3, /CMD_LOGIN, /cpanel
  6. MX-record vs A-record coincidence (cPanel default mail config)

Idempotent: skips already-fingerprinted domains unless --force.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir  # noqa: E402
from lib.http import polite_client, PoliteClient  # noqa: E402
from lib.jsonl import append_record, iter_records, read_existing_keys  # noqa: E402
from lib.panel import fingerprint, PATH_PROBES  # noqa: E402

try:
    import dns.asyncresolver
    import dns.exception
    import dns.reversename
except ImportError:
    dns = None  # type: ignore

DETECTIONS_PATH = data_dir() / "detections.jsonl"
LIVE_PATH = data_dir() / "live.jsonl"
PANELS_PATH = data_dir() / "panels.jsonl"


# ---------- Sync TLS cert inspection ----------
def get_cert_issuer(host: str, port: int = 443, timeout: float = 6.0) -> str:
    """Open a TLS connection and return the cert issuer subject (best-effort)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                if not cert:
                    return ""
                issuer = cert.get("issuer", ())
                # tuple of tuples of (key, value); flatten to "key=value, key=value"
                parts = []
                for rdn in issuer:
                    for k, v in rdn:
                        parts.append(f"{k}={v}")
                return ", ".join(parts)
    except Exception:
        return ""


async def acert_issuer(host: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_cert_issuer, host)


# ---------- Reverse PTR ----------
async def reverse_ptr(ip: str | None) -> str:
    if not ip or dns is None:
        return ""
    try:
        rev = dns.reversename.from_address(ip)
    except Exception:
        return ""
    try:
        resolver = dns.asyncresolver.Resolver(configure=False)
        resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
        resolver.timeout = 3.0
        resolver.lifetime = 6.0
        ans = await resolver.resolve(rev, "PTR")
        return ans[0].to_text().rstrip(".")
    except (dns.exception.DNSException, Exception):
        return ""


# ---------- MX vs A coincidence ----------
async def mx_a_match(domain: str, ip: str | None) -> bool:
    if not ip or dns is None:
        return False
    try:
        resolver = dns.asyncresolver.Resolver(configure=False)
        resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
        resolver.timeout = 3.0
        resolver.lifetime = 6.0
        mx = await resolver.resolve(domain, "MX")
        for r in mx:
            try:
                target = str(r.exchange).rstrip(".")
                a = await resolver.resolve(target, "A")
                for ar in a:
                    if ar.to_text() == ip:
                        return True
            except dns.exception.DNSException:
                continue
    except dns.exception.DNSException:
        return False
    except Exception:
        return False
    return False


# ---------- HTTP probes ----------
async def probe_paths(client: PoliteClient, base: str) -> dict[str, tuple[int, str]]:
    """Run all PATH_PROBES against `base://host`. Returns {path: (status, body[:8KB])}."""
    out: dict[str, tuple[int, str]] = {}
    for path, _panel, _matcher in PATH_PROBES:
        url = base.rstrip("/") + path
        try:
            resp = await client.get(url)
        except Exception:
            out[path] = (0, "")
            continue
        if resp is None:
            out[path] = (0, "")
            continue
        body = (resp.text or "")[:8192]
        out[path] = (resp.status_code, body)
    return out


async def probe_homepage(client: PoliteClient, base: str) -> tuple[str, str]:
    """Returns (server_header, body_first_16KB)."""
    try:
        resp = await client.get(base.rstrip("/") + "/")
    except Exception:
        return "", ""
    if resp is None:
        return "", ""
    server = resp.headers.get("server", "") or resp.headers.get("Server", "")
    body = (resp.text or "")[:16384]
    return server, body


# ---------- Per-domain orchestration ----------
async def fingerprint_one(
    client: PoliteClient,
    domain: str,
    ip: str | None,
    scheme: str,
) -> dict:
    base = f"{scheme}://{domain}"
    # Run network calls roughly in parallel where possible.
    home_task = asyncio.create_task(probe_homepage(client, base))
    paths_task = asyncio.create_task(probe_paths(client, base))
    cert_task = asyncio.create_task(acert_issuer(domain)) if scheme == "https" else None
    ptr_task = asyncio.create_task(reverse_ptr(ip))
    mx_task = asyncio.create_task(mx_a_match(domain, ip))

    server_hdr, home_body = await home_task
    path_results = await paths_task
    cert_issuer = await cert_task if cert_task else ""
    ptr = await ptr_task
    mx_match = await mx_task

    body_samples = [home_body]
    for _path, (_st, body) in path_results.items():
        if body:
            body_samples.append(body)

    panel, evidence = fingerprint(
        server_header=server_hdr,
        body_samples=body_samples,
        cert_issuer=cert_issuer,
        ptr=ptr,
        path_results=path_results,
        mx_a_match=mx_match,
    )
    return {
        "domain": domain,
        "host_panel": panel,
        "server_header": server_hdr,
        "cert_issuer": cert_issuer,
        "reverse_ptr": ptr,
        "mx_a_match": mx_match,
        "evidence": evidence,
        "fingerprinted_at": datetime.now(timezone.utc).isoformat(),
    }


async def run(force: bool, limit: int | None, concurrency: int) -> int:
    cfg = Config.from_env()
    if not DETECTIONS_PATH.exists():
        print("[panels] no detections.jsonl; run stage 03 first", file=sys.stderr)
        return 0
    seen = set() if force else read_existing_keys(PANELS_PATH, "domain")
    live_index: dict[str, dict] = {r["domain"]: r for r in iter_records(LIVE_PATH) if r.get("domain")}
    targets: list[tuple[str, str | None, str]] = []
    for rec in iter_records(DETECTIONS_PATH):
        d = rec.get("domain")
        if not isinstance(d, str) or d in seen:
            continue
        scheme = rec.get("scheme") or "https"
        ip = (live_index.get(d) or {}).get("ip")
        targets.append((d, ip, scheme))
        if limit and len(targets) >= limit:
            break
    print(f"[panels] fingerprinting {len(targets)} domains (concurrency={concurrency})",
          file=sys.stderr)
    if not targets:
        return 0

    written = 0
    sem = asyncio.Semaphore(concurrency)

    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=cfg.rps_per_host,
        timeout=cfg.timeout,
        max_concurrent=200,
    ) as client:

        async def worker(d: str, ip: str | None, scheme: str) -> None:
            nonlocal written
            async with sem:
                try:
                    rec = await fingerprint_one(client, d, ip, scheme)
                except Exception as exc:
                    rec = {
                        "domain": d,
                        "host_panel": None,
                        "error": str(exc)[:200],
                        "fingerprinted_at": datetime.now(timezone.utc).isoformat(),
                    }
            append_record(PANELS_PATH, rec)
            written += 1
            if written % 50 == 0:
                print(f"[panels] {written}/{len(targets)} done", file=sys.stderr)

        await asyncio.gather(*(worker(d, ip, sc) for d, ip, sc in targets))

    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=100)
    args = p.parse_args()
    n = asyncio.run(run(args.force, args.limit, args.concurrency))
    print(f"[panels] wrote {n} records to {PANELS_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
