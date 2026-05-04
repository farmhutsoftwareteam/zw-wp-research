"""Async DNS resolver with CDN tagging."""
from __future__ import annotations

import asyncio
import ipaddress
from typing import Any

try:
    import dns.asyncresolver
    import dns.exception
    import dns.rdatatype
    import dns.resolver
except ImportError:  # pragma: no cover
    dns = None  # type: ignore[assignment]


# CIDR ranges for known CDNs / hosts. Subset; expand as needed.
_CDN_RANGES: list[tuple[str, ipaddress.IPv4Network]] = [
    ("cloudflare", ipaddress.IPv4Network("104.16.0.0/12")),
    ("cloudflare", ipaddress.IPv4Network("172.64.0.0/13")),
    ("cloudflare", ipaddress.IPv4Network("173.245.48.0/20")),
    ("cloudflare", ipaddress.IPv4Network("103.21.244.0/22")),
    ("cloudflare", ipaddress.IPv4Network("103.22.200.0/22")),
    ("cloudflare", ipaddress.IPv4Network("103.31.4.0/22")),
    ("cloudflare", ipaddress.IPv4Network("141.101.64.0/18")),
    ("cloudflare", ipaddress.IPv4Network("108.162.192.0/18")),
    ("cloudflare", ipaddress.IPv4Network("190.93.240.0/20")),
    ("cloudflare", ipaddress.IPv4Network("188.114.96.0/20")),
    ("cloudflare", ipaddress.IPv4Network("197.234.240.0/22")),
    ("cloudflare", ipaddress.IPv4Network("198.41.128.0/17")),
    ("cloudflare", ipaddress.IPv4Network("162.158.0.0/15")),
    ("cloudflare", ipaddress.IPv4Network("131.0.72.0/22")),
    ("fastly", ipaddress.IPv4Network("151.101.0.0/16")),
    ("fastly", ipaddress.IPv4Network("199.232.0.0/16")),
    ("akamai", ipaddress.IPv4Network("23.32.0.0/11")),
    ("akamai", ipaddress.IPv4Network("23.192.0.0/11")),
    ("akamai", ipaddress.IPv4Network("104.64.0.0/10")),
    ("aws-cloudfront", ipaddress.IPv4Network("13.32.0.0/15")),
    ("aws-cloudfront", ipaddress.IPv4Network("52.84.0.0/15")),
    ("google", ipaddress.IPv4Network("142.250.0.0/15")),
    ("google", ipaddress.IPv4Network("34.96.0.0/12")),
    ("vercel", ipaddress.IPv4Network("76.76.21.0/24")),
    ("netlify", ipaddress.IPv4Network("75.2.60.0/24")),
]


def cdn_for_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if not isinstance(addr, ipaddress.IPv4Address):
        return None
    for name, net in _CDN_RANGES:
        if addr in net:
            return name
    return None


_RESOLVER: "dns.asyncresolver.Resolver | None" = None


def _resolver() -> "dns.asyncresolver.Resolver":
    global _RESOLVER
    if dns is None:
        raise RuntimeError("dnspython is not installed")
    if _RESOLVER is None:
        r = dns.asyncresolver.Resolver(configure=False)
        r.nameservers = ["1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4"]
        r.timeout = 3.0
        r.lifetime = 6.0
        _RESOLVER = r
    return _RESOLVER


async def _query(name: str, rdtype: str) -> list[str]:
    try:
        answer = await _resolver().resolve(name, rdtype)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers,
            dns.exception.Timeout, dns.exception.DNSException):
        return []
    return [r.to_text() for r in answer]


async def resolve(domain: str) -> dict[str, Any] | None:
    """Resolve domain → {ip, ns, cname} or None on terminal failure.

    Tries A, then AAAA, then CNAME. Captures NS for additional signal.
    """
    if dns is None:
        raise RuntimeError("dnspython is not installed")
    a = await _query(domain, "A")
    aaaa: list[str] = []
    cname: list[str] = []
    if not a:
        cname = await _query(domain, "CNAME")
        aaaa = await _query(domain, "AAAA")
    if not a and not aaaa and not cname:
        return None
    # Get NS too — useful for fingerprinting hosting
    ns = await _query(domain, "NS")
    ip = a[0] if a else (aaaa[0] if aaaa else None)
    return {
        "ip": ip,
        "a": a,
        "aaaa": aaaa,
        "cname": cname[0].rstrip(".") if cname else None,
        "ns": [n.rstrip(".") for n in ns],
    }


async def resolve_many(domains: list[str], concurrency: int = 1000) -> dict[str, dict[str, Any] | None]:
    """Resolve a batch in parallel. Returns dict[domain, result-or-None]."""
    sem = asyncio.Semaphore(concurrency)

    async def one(d: str) -> tuple[str, dict[str, Any] | None]:
        async with sem:
            try:
                return d, await resolve(d)
            except Exception:
                return d, None

    out: dict[str, dict[str, Any] | None] = {}
    for coro in asyncio.as_completed([one(d) for d in domains]):
        d, res = await coro
        out[d] = res
    return out
