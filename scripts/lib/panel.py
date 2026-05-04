"""Hosting control-panel fingerprinting.

Combines six signal types into a single panel verdict:
  1. HTTP `Server:` response header
  2. HTML body markers (cPanel/Plesk login HTML, license URLs)
  3. TLS certificate issuer (cPanel AutoSSL is a giveaway)
  4. Reverse DNS PTR (hosting-brand patterns: gator..., cpanel..., plesk...)
  5. Path probes (`/.well-known/cpanel-dcv/`, `/cgi-sys/defaultwebpage.cgi`,
     `/plesk-stat/login.php3`, `/CMD_LOGIN`, `/whm`)
  6. MX-record vs A-record same-IP coincidence (weak; cPanel default)

Returns a single canonical panel name plus an evidence dict so the site can
show *why* we think it's cPanel.
"""
from __future__ import annotations

import re
from collections import Counter

# ---- Server-header patterns ----
SERVER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bcpsrvd\b", re.I), "cpanel"),
    (re.compile(r"\bsw-cp-server\b", re.I), "plesk"),
    (re.compile(r"\bDirectAdmin\b", re.I), "directadmin"),
    (re.compile(r"\bMiniServ\b", re.I), "webmin"),
    # LiteSpeed strongly correlates with cPanel/CloudLinux shared hosting,
    # but isn't proof. Recorded as weak signal — promoted to "cpanel" only if
    # corroborated by another signal.
    (re.compile(r"\bLiteSpeed\b", re.I), "litespeed"),
    (re.compile(r"\bLSWS\b", re.I), "litespeed"),
    (re.compile(r"\bnginx-cpanel\b", re.I), "cpanel"),
]

# ---- HTML body markers (case-insensitive) ----
BODY_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"cPanel\s*&\s*WHM|cpsess\d+|cpanel\.com/license|cpanel-branding", re.I), "cpanel"),
    (re.compile(r"Plesk\s+(Onyx|Obsidian|Web\s*Host)|/plesk-(stat|site-preview)/|plesk-control-panel", re.I), "plesk"),
    (re.compile(r"DirectAdmin\s+Login|directadmin\.com", re.I), "directadmin"),
    (re.compile(r"Webmin\s+Login|/webmin\.cgi", re.I), "webmin"),
    (re.compile(r"HestiaCP|Hestia\s+Control\s+Panel", re.I), "hestia"),
    (re.compile(r"Vesta\s+Control\s+Panel", re.I), "vesta"),
    # cPanel-distributed default placeholder pages
    (re.compile(r"This\s+is\s+the\s+default\s+welcome\s+page.*cPanel", re.I | re.S), "cpanel"),
    (re.compile(r"cgi-sys/defaultwebpage\.cgi", re.I), "cpanel"),
]

# ---- TLS cert issuer / subject patterns ----
CERT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"cPanel,?\s*Inc\.?\s*Certification\s*Authority", re.I), "cpanel"),
    (re.compile(r"cPanel\s*Inc\.?\s*Certification", re.I), "cpanel"),
    (re.compile(r"Plesk\s+(Auto)?SSL", re.I), "plesk"),
]

# ---- Reverse-PTR patterns: hosting-brand → likely-panel ----
PTR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # cPanel-typical shared hosts
    (re.compile(r"\b(gator\d+|hostgator|bluehost|hostmonster|justhost|fatcow|ipage)\b", re.I), "cpanel"),
    (re.compile(r"\b(namecheap|stableserver|webhostbox|inmotionhosting|a2hosting|hawkhost|hostinger)\b", re.I), "cpanel"),
    (re.compile(r"\b(siteground|sg-shared|sg2plzcpnl|sgcpanel|sgvps\.net)\b", re.I), "cpanel"),
    (re.compile(r"\b(p3plzcpnl|p3plcpnl|godaddy|secureserver)\b", re.I), "cpanel"),
    (re.compile(r"\b(server\d+\.(?:web-hosting|hostgator|cpanel|hostmonster|inmotionhosting)\b)", re.I), "cpanel"),
    (re.compile(r"\bcpanel\b", re.I), "cpanel"),
    (re.compile(r"\bwhm\b", re.I), "cpanel"),
    # Plesk
    (re.compile(r"\bplesk\b", re.I), "plesk"),
    # DirectAdmin
    (re.compile(r"\bdirectadmin\b", re.I), "directadmin"),
]

# ---- Path-based signals ----
# Map: probed path → list of (matcher, panel) where matcher is a callable
# (status_code, body) → bool.
def _match_cpanel_dcv(status: int, body: str) -> bool:
    # cPanel always serves this directory if the host is cPanel-managed —
    # often returns 200 with index of, or 403 forbidden. Either is a positive.
    return status in (200, 401, 403)


def _match_default_webpage(status: int, body: str) -> bool:
    return status == 200 and ("cPanel" in (body or "") or "default welcome" in (body or "").lower())


def _match_plesk_stat(status: int, body: str) -> bool:
    return status in (200, 401, 403) or ("plesk" in (body or "").lower())


def _match_cmd_login(status: int, body: str) -> bool:
    return status == 200 and "DirectAdmin" in (body or "")


def _match_whm(status: int, body: str) -> bool:
    return "whm" in (body or "").lower() or status in (401, 403)


PATH_PROBES: list[tuple[str, str, callable]] = [
    ("/.well-known/cpanel-dcv/", "cpanel", _match_cpanel_dcv),
    ("/cgi-sys/defaultwebpage.cgi", "cpanel", _match_default_webpage),
    ("/plesk-stat/login.php3", "plesk", _match_plesk_stat),
    ("/CMD_LOGIN", "directadmin", _match_cmd_login),
    ("/cpanel", "cpanel", _match_default_webpage),  # often 200 cPanel HTML
]


# Verdict precedence — higher beats lower when multiple panels detected.
PRIORITY: dict[str, int] = {
    "cpanel": 10,
    "plesk": 10,
    "directadmin": 9,
    "hestia": 7,
    "vesta": 7,
    "webmin": 5,
    "litespeed": 1,
    None: 0,
}


def fingerprint(
    *,
    server_header: str = "",
    body_samples: list[str] | None = None,
    cert_issuer: str = "",
    ptr: str = "",
    path_results: dict[str, tuple[int, str]] | None = None,
    mx_a_match: bool = False,
) -> tuple[str | None, dict]:
    """Combine signals → (panel, evidence).

    Args:
        server_header: HTTP `Server:` header text.
        body_samples: HTML bodies (homepage + probe responses).
        cert_issuer: TLS cert issuer subject.
        ptr: reverse DNS hostname for the IP.
        path_results: dict[path -> (status_code, body)] from probe requests.
        mx_a_match: True if any MX record IP equals an A record IP (cPanel default).
    """
    evidence: dict = {
        "server": server_header,
        "cert_issuer": cert_issuer,
        "ptr": ptr,
        "matches": [],
    }
    votes: Counter = Counter()
    weights = {
        "server": 4,
        "cert": 5,
        "body": 3,
        "ptr": 2,
        "path": 4,
        "mx": 1,
    }

    # 1. Server header
    if server_header:
        for pat, name in SERVER_PATTERNS:
            if pat.search(server_header):
                votes[name] += weights["server"]
                evidence["matches"].append(f"server:{name}")
                break

    # 2. Body samples
    for body in (body_samples or []):
        if not body:
            continue
        for pat, name in BODY_MARKERS:
            if pat.search(body):
                votes[name] += weights["body"]
                evidence["matches"].append(f"body:{name}")

    # 3. Cert issuer
    if cert_issuer:
        for pat, name in CERT_PATTERNS:
            if pat.search(cert_issuer):
                votes[name] += weights["cert"]
                evidence["matches"].append(f"cert:{name}")

    # 4. Reverse PTR
    if ptr:
        for pat, name in PTR_PATTERNS:
            if pat.search(ptr):
                votes[name] += weights["ptr"]
                evidence["matches"].append(f"ptr:{name}")
                break

    # 5. Path probes
    if path_results:
        for path, candidate_panel, matcher in PATH_PROBES:
            res = path_results.get(path)
            if not res:
                continue
            status, body = res
            try:
                if matcher(status, body or ""):
                    votes[candidate_panel] += weights["path"]
                    evidence["matches"].append(f"path:{path}->{candidate_panel}")
            except Exception:
                continue

    # 6. MX/A coincidence — weakest signal, only if we already have other votes
    if mx_a_match and any(v >= weights["server"] for v in votes.values()):
        # nudge toward cpanel/plesk (whichever already leads)
        leader = max(votes.items(), key=lambda kv: kv[1])[0]
        votes[leader] += weights["mx"]
        evidence["matches"].append(f"mx-a-coincidence:+{leader}")

    if not votes:
        return None, evidence

    # LiteSpeed alone isn't enough — needs corroboration to promote to a panel.
    if list(votes.keys()) == ["litespeed"]:
        return "litespeed", evidence

    # Pick the highest-vote candidate, breaking ties by PRIORITY.
    best = max(votes.items(), key=lambda kv: (kv[1], PRIORITY.get(kv[0], 0)))
    panel = best[0]
    evidence["votes"] = dict(votes)
    evidence["winner"] = panel
    return panel, evidence
