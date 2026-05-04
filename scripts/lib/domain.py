"""Domain normalization and classification helpers."""
from __future__ import annotations

import re
from urllib.parse import urlparse

_PROTO = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def normalize(value: str) -> str | None:
    """Lowercase, strip protocol/path/port/trailing dot, IDNA-encode.

    Returns None if the input doesn't look like a hostname at all.
    """
    if not value:
        return None
    s = value.strip().lower()
    # Strip protocol if present
    if _PROTO.match(s):
        try:
            parsed = urlparse(s)
            s = parsed.hostname or ""
        except ValueError:
            return None
    # Take first path segment off if user pasted "example.com/foo"
    s = s.split("/", 1)[0]
    # Strip port
    s = s.split(":", 1)[0]
    # Strip trailing dot
    s = s.rstrip(".")
    # Strip leading "www." — we want the apex form for dedup
    if s.startswith("www."):
        s = s[4:]
    if not s or "." not in s:
        return None
    # Reject IPs
    if re.fullmatch(r"[\d.]+", s) or ":" in s:
        return None
    # IDNA encode
    try:
        s.encode("idna")
    except UnicodeError:
        return None
    # Sanity: only allow [a-z0-9.-] after normalization
    if not re.fullmatch(r"[a-z0-9.\-]+", s):
        return None
    return s


def is_zw_tld(domain: str) -> bool:
    """True if the domain is under .zw (any second-level)."""
    if not domain:
        return False
    return domain.endswith(".zw")


# Body-content SHA1 prefixes of well-known parking pages.
# Stage 02 uses IPs; stage 03 can compare HTML body hash against these.
PARKING_HASHES: set[str] = {
    # Sedo / GoDaddy / Namecheap default landers — placeholder set, expanded over time.
}

# IPs known to host only parking / for-sale placeholders.
PARKING_IPS: set[str] = {
    "199.59.243.222",   # Sedo
    "199.59.243.223",
    "13.248.169.48",    # Sedo AWS
    "76.223.105.230",
    "208.91.197.27",    # ConfluenceNet (Bodis)
    "208.91.197.39",
    "44.221.72.231",    # Namecheap parking
}


def is_parking_ip(ip: str | None) -> bool:
    return bool(ip and ip in PARKING_IPS)
