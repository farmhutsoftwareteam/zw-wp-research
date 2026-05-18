#!/usr/bin/env python3
"""Stage 12 — Version audit (purely from existing data).

Reads what we already collected (server_header, wp_version, panel_evidence)
and produces a software-version audit grouped by host_panel. NO new HTTP
requests — this is just SQL + regex over reports/zwwp.db.

Output:
    reports/cpanel_version_audit.csv    — per-site software versions seen
    reports/cpanel_version_summary.md   — frequency tables, "what's old" list

Useful for the engagement deliverable to GoZ:
  - distribution of Apache / LiteSpeed / nginx versions across cPanel sites
  - WordPress core version distribution
  - "looks visibly out of date" list — sites worth phoning first

Note: the public surface rarely exposes the cPanel/WHM version itself. We
can see Apache/LiteSpeed/nginx + WordPress; we cannot see WHM version
without probing the control plane (which is out of scope).
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import reports_dir  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
CSV_OUT = reports_dir() / "cpanel_version_audit.csv"
MD_OUT = reports_dir() / "cpanel_version_summary.md"


# ---- Version extractors ----
APACHE_RE = re.compile(r"Apache(?:/([\d\.]+))?", re.I)
LITESPEED_RE = re.compile(r"LiteSpeed(?:/([\d\.]+))?", re.I)
NGINX_RE = re.compile(r"nginx(?:/([\d\.]+))?", re.I)
OS_RE = re.compile(r"\(([^)]+)\)")
PHP_RE = re.compile(r"PHP/([\d\.]+)", re.I)


def parse_server_header(s: str) -> dict:
    if not s:
        return {}
    out: dict = {}
    m = APACHE_RE.search(s)
    if m:
        out["http_server"] = "apache"
        if m.group(1):
            out["http_version"] = m.group(1)
    m = LITESPEED_RE.search(s)
    if m:
        out["http_server"] = "litespeed"
        if m.group(1):
            out["http_version"] = m.group(1)
    m = NGINX_RE.search(s)
    if m and "http_server" not in out:
        out["http_server"] = "nginx"
        if m.group(1):
            out["http_version"] = m.group(1)
    m = OS_RE.search(s)
    if m:
        os_text = m.group(1).strip().lower()
        for os_label in ("ubuntu", "debian", "centos", "rocky", "almalinux", "rhel", "amzn"):
            if os_label in os_text:
                out["os"] = os_label
                break
    m = PHP_RE.search(s)
    if m:
        out["php_version"] = m.group(1)
    return out


def parse_version_tuple(v: str | None) -> tuple[int, ...]:
    if not v:
        return (0,)
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts) or (0,)


def is_likely_outdated(http_server: str | None, http_version: str | None,
                       wp_version: str | None) -> tuple[bool, list[str]]:
    """Heuristic — flag versions clearly behind current GA series.

    These are intentionally lenient to avoid false positives. The intent is
    'this owner should be reminded to patch', not 'this is exploitable'.
    """
    flags: list[str] = []
    if http_server == "apache" and http_version:
        v = parse_version_tuple(http_version)
        if v < (2, 4, 50):
            flags.append(f"apache_old({http_version})")
    if http_server == "litespeed" and http_version:
        v = parse_version_tuple(http_version)
        if v and v < (6, 0, 0):
            flags.append(f"litespeed_old({http_version})")
    if http_server == "nginx" and http_version:
        v = parse_version_tuple(http_version)
        if v and v < (1, 24, 0):
            flags.append(f"nginx_old({http_version})")
    if wp_version:
        v = parse_version_tuple(wp_version)
        if v and v < (6, 4, 0):
            flags.append(f"wp_old({wp_version})")
    return bool(flags), flags


def run() -> None:
    if not DB_PATH.exists():
        print(f"[version] {DB_PATH} not found; run stage 08 first", file=sys.stderr)
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        SELECT domain, host_panel, score, tranco_rank, category, theme,
               server_header, wp_version, ip, cdn
        FROM domains
        WHERE score >= 70
        ORDER BY (tranco_rank IS NULL), tranco_rank, domain
    """)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    print(f"[version] {len(rows)} WP-positive sites in scope", file=sys.stderr)

    # Per-row CSV
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "domain", "host_panel", "tranco_rank", "category", "theme",
            "http_server", "http_version", "os", "wp_version",
            "outdated_flags", "ip", "cdn",
        ])
        for r in rows:
            parsed = parse_server_header(r.get("server_header") or "")
            old, flags = is_likely_outdated(
                parsed.get("http_server"),
                parsed.get("http_version"),
                r.get("wp_version"),
            )
            r["_parsed"] = parsed
            r["_outdated"] = old
            r["_flags"] = flags
            w.writerow([
                r["domain"],
                r.get("host_panel") or "",
                r.get("tranco_rank") or "",
                r.get("category") or "",
                r.get("theme") or "",
                parsed.get("http_server") or "",
                parsed.get("http_version") or "",
                parsed.get("os") or "",
                r.get("wp_version") or "",
                ";".join(flags),
                r.get("ip") or "",
                r.get("cdn") or "",
            ])

    # Markdown summary
    cpanel_rows = [r for r in rows if (r.get("host_panel") or "") == "cpanel"]
    http_servers = Counter((r["_parsed"].get("http_server") or "unknown") for r in cpanel_rows)
    http_versions = Counter()
    for r in cpanel_rows:
        v = r["_parsed"].get("http_version")
        if v:
            http_versions[v] += 1
    wp_versions = Counter()
    for r in cpanel_rows:
        if r.get("wp_version"):
            wp_versions[r["wp_version"]] += 1
    os_dist = Counter((r["_parsed"].get("os") or "unknown") for r in cpanel_rows)
    outdated = [r for r in cpanel_rows if r["_outdated"]]

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in outdated:
        by_cat[r.get("category") or "uncategorized"].append(r)

    lines: list[str] = []
    lines.append("# cPanel software-version audit")
    lines.append("")
    lines.append("Snapshot of HTTP server and WordPress versions across the **cPanel-positive**")
    lines.append("Zimbabwean WordPress sites in our dataset. Built entirely from the homepage")
    lines.append("`Server:` header and the WordPress `<meta name=\"generator\">` tag we recorded")
    lines.append("during the original detection pass — **no new HTTP requests were made for**")
    lines.append("**this audit**.")
    lines.append("")
    lines.append(f"- Total cPanel-positive WP sites: **{len(cpanel_rows)}**")
    lines.append(f"- Sites with at least one visibly old version: **{len(outdated)}** ({len(outdated)*100//max(1, len(cpanel_rows))}%)")
    lines.append("")
    lines.append("## HTTP server distribution")
    lines.append("")
    lines.append("| Server | Sites |")
    lines.append("|---|---|")
    for k, n in http_servers.most_common():
        lines.append(f"| {k} | {n} |")
    lines.append("")
    if http_versions:
        lines.append("## HTTP server versions (top 15)")
        lines.append("")
        lines.append("| Version | Sites |")
        lines.append("|---|---|")
        for v, n in http_versions.most_common(15):
            lines.append(f"| `{v}` | {n} |")
        lines.append("")
    if os_dist:
        lines.append("## Underlying OS (where Server header reveals it)")
        lines.append("")
        lines.append("| OS | Sites |")
        lines.append("|---|---|")
        for k, n in os_dist.most_common():
            lines.append(f"| {k} | {n} |")
        lines.append("")
    if wp_versions:
        lines.append("## WordPress core version distribution (top 20)")
        lines.append("")
        lines.append("| WP version | Sites |")
        lines.append("|---|---|")
        for v, n in wp_versions.most_common(20):
            lines.append(f"| `{v}` | {n} |")
        lines.append("")
    if outdated:
        lines.append("## Sites running visibly out-of-date software (priority outreach)")
        lines.append("")
        lines.append("Sorted by Tranco rank — call/email these first. The flag column shows")
        lines.append("which version trail looks behind GA.")
        lines.append("")
        lines.append("| Domain | Rank | Category | HTTP | WP | Flags |")
        lines.append("|---|---|---|---|---|---|")
        outdated.sort(key=lambda r: (r.get("tranco_rank") or 10**9))
        for r in outdated[:50]:
            lines.append("| {dom} | {rk} | {cat} | {hs}/{hv} | {wp} | {fl} |".format(
                dom=r["domain"],
                rk=r.get("tranco_rank") or "—",
                cat=r.get("category") or "—",
                hs=r["_parsed"].get("http_server") or "—",
                hv=r["_parsed"].get("http_version") or "—",
                wp=r.get("wp_version") or "—",
                fl=", ".join(r["_flags"]),
            ))
        lines.append("")
    MD_OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[version] wrote {CSV_OUT}", file=sys.stderr)
    print(f"[version] wrote {MD_OUT}", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.parse_args()
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
