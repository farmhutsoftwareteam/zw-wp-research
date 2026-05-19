"""WhatsApp deeplink helpers.

ZW SMBs live on WhatsApp Business. Phones we already collected (3,808 of
them) can be turned into one-click WA chat openers via the `wa.me/<digits>`
deeplink with optional pre-filled message text.
"""
from __future__ import annotations

import re
from urllib.parse import quote


# Zim country code; we also accept South African (+27) numbers because some
# ZW businesses operate from SA. Extend as scope grows.
DEFAULT_COUNTRY_CODES = ("263", "27")


def normalise_phone(raw: str, *, default_cc: str = "263") -> str | None:
    """Strip a phone number to digits only, prepending the country code if
    missing. Returns None for unrecognisable inputs.

    Rules:
      - +263 77 123 4567       → 263771234567
      - 0771234567             → 263771234567 (assume ZW)
      - 077 123 4567           → 263771234567
      - +27 11 234 5678        → 27112345678
      - 7712345678             → 263771234567 (assume ZW, missing leading 0)
      - junk                   → None
    """
    if not raw:
        return None
    s = re.sub(r"[^\d+]", "", raw)
    if not s:
        return None
    if s.startswith("+"):
        s = s[1:]
        if not s.isdigit():
            return None
        if len(s) < 9 or len(s) > 15:
            return None
        return s
    if not s.isdigit():
        return None
    # Already starts with a known country code
    for cc in DEFAULT_COUNTRY_CODES:
        if s.startswith(cc) and len(s) >= 11:
            return s
    if s.startswith("0"):
        s = s[1:]
    if len(s) < 8 or len(s) > 12:
        return None
    return default_cc + s


def wa_link(phone: str, message: str | None = None) -> str | None:
    """Build a `https://wa.me/<digits>?text=...` deeplink.

    Returns None if phone can't be normalised.
    """
    digits = normalise_phone(phone)
    if not digits:
        return None
    url = f"https://wa.me/{digits}"
    if message:
        url += "?text=" + quote(message)
    return url


def default_opener(*, name: str | None, domain: str, pain: str | None = None) -> str:
    """A generic opening message — short, polite, ZW-friendly."""
    greeting = f"Hi {name.split()[0]}" if name else "Hello"
    site = domain
    if pain:
        return (
            f"{greeting} — Munya here. I noticed something about your site "
            f"{site} that's worth a 2-min chat: {pain}. Would today suit you?"
        )
    return (
        f"{greeting}, this is Munya. I'm reaching out about your site {site} "
        f"with a quick observation that might save you some headaches. "
        f"Is now a good time to talk for 2 minutes?"
    )
