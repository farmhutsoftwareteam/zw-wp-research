#!/usr/bin/env python3
"""Stage 07 — Reporter.

Joins enriched/classified/verified by domain, produces:
- reports/report.md     narrative summary by category, top 10 per category, plugin frequencies
- reports/top_zw_wordpress.csv  flat CSV of all WP-positive sites

With --prose, adds a Claude-written intro/findings via `claude -p` (uses Max sub).
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.claude_cli import is_available, run_claude, ClaudeError  # noqa: E402
from lib.jsonl import iter_records  # noqa: E402

ENRICHED_PATH = data_dir() / "enriched.jsonl"
CLASSIFIED_PATH = data_dir() / "classified.jsonl"
VERIFIED_PATH = data_dir() / "verified.jsonl"
REPORT_MD = reports_dir() / "report.md"
REPORT_CSV = reports_dir() / "top_zw_wordpress.csv"


def _load_joined() -> list[dict]:
    """Inner-join verified ⨝ classified ⨝ enriched on domain."""
    enriched = {r["domain"]: r for r in iter_records(ENRICHED_PATH) if r.get("domain")}
    classified = {r["domain"]: r for r in iter_records(CLASSIFIED_PATH) if r.get("domain")}
    verified = {r["domain"]: r for r in iter_records(VERIFIED_PATH) if r.get("domain")}
    out = []
    seen: set[str] = set()
    # Prefer verified; fall back to classified+enriched merged
    for d, vrec in verified.items():
        merged = {**enriched.get(d, {}), **classified.get(d, {}), **vrec}
        out.append(merged)
        seen.add(d)
    for d, crec in classified.items():
        if d in seen:
            continue
        merged = {**enriched.get(d, {}), **crec}
        out.append(merged)
        seen.add(d)
    for d, erec in enriched.items():
        if d in seen:
            continue
        out.append(erec)
    return out


def _section_top_n(rows: list[dict], category: str, n: int = 10) -> list[dict]:
    return sorted(
        [r for r in rows if r.get("category") == category],
        key=lambda r: (r.get("tranco_rank") or 10**9),
    )[:n]


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _format_plugins(plugins: list[str]) -> str:
    if not plugins:
        return ""
    if len(plugins) <= 3:
        return ", ".join(plugins)
    return ", ".join(plugins[:3]) + f" +{len(plugins) - 3}"


def _build_report(rows: list[dict], use_prose: bool, top_per_cat: int) -> str:
    rows_by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        rows_by_cat[r.get("category") or "other"].append(r)

    plugin_counter: Counter = Counter()
    theme_counter: Counter = Counter()
    for r in rows:
        for p in r.get("plugins") or []:
            plugin_counter[p] += 1
        if r.get("theme"):
            theme_counter[r["theme"]] += 1

    parts: list[str] = []
    parts.append("# Zimbabwe WordPress sites — research report")
    parts.append("")
    parts.append(f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    parts.append("")
    parts.append(f"- Total WP-positive domains analyzed: **{len(rows)}**")
    parts.append(f"- Verified with Playwright: **{sum(1 for r in rows if r.get('verified_at'))}**")
    parts.append(f"- Categorized with Claude (Haiku): **{sum(1 for r in rows if r.get('category'))}**")
    parts.append("")

    if use_prose and is_available():
        prose = _claude_prose(rows, plugin_counter, theme_counter)
        if prose:
            parts.append("## Findings")
            parts.append("")
            parts.append(prose.strip())
            parts.append("")

    parts.append("## Methodology")
    parts.append("")
    parts.append("1. Seed harvest: Tranco top-1M (.zw filter) + Common Crawl CDX + curated scrapes (techzim.co.zw, pindula.co.zw, gov.zw).")
    parts.append("2. DNS resolution against 1.1.1.1 / 8.8.8.8; CDN tagging by IP CIDR; parking-IP filter.")
    parts.append("3. WordPress detection: 5 probe paths, 12 weighted signals, threshold score >= 70.")
    parts.append("4. Traffic enrichment: Tranco rank; optional Cloudflare Radar bucket.")
    parts.append("5. Categorization: Claude Haiku via `claude -p` (Max plan), batched 20.")
    parts.append("6. Verification: Playwright headless render at 1440×900, screenshot + asset URL fingerprinting.")
    parts.append("")

    cat_order = sorted(rows_by_cat.keys(), key=lambda c: (-len(rows_by_cat[c]), c))
    parts.append("## Categories")
    parts.append("")
    parts.append(_format_table(
        ["Category", "Count"],
        [[c, str(len(rows_by_cat[c]))] for c in cat_order],
    ))
    parts.append("")

    for cat in cat_order:
        top = sorted(
            rows_by_cat[cat],
            key=lambda r: (r.get("tranco_rank") or 10**9),
        )[:top_per_cat]
        if not top:
            continue
        parts.append(f"### {cat.title()} — top {len(top)}")
        parts.append("")
        table_rows = []
        for r in top:
            table_rows.append([
                f"[{r['domain']}](https://{r['domain']}/)",
                str(r.get("tranco_rank") or "—"),
                str(r.get("score") or "—"),
                r.get("theme") or "—",
                _format_plugins(r.get("plugins") or []),
            ])
        parts.append(_format_table(
            ["Domain", "Tranco rank", "Score", "Theme", "Plugins"],
            table_rows,
        ))
        parts.append("")

    if plugin_counter:
        parts.append("## Plugin frequency (top 25)")
        parts.append("")
        parts.append(_format_table(
            ["Plugin", "Sites"],
            [[name, str(n)] for name, n in plugin_counter.most_common(25)],
        ))
        parts.append("")

    if theme_counter:
        parts.append("## Theme frequency (top 15)")
        parts.append("")
        parts.append(_format_table(
            ["Theme", "Sites"],
            [[name, str(n)] for name, n in theme_counter.most_common(15)],
        ))
        parts.append("")

    return "\n".join(parts)


def _claude_prose(rows: list[dict], plugins: Counter, themes: Counter) -> str | None:
    cfg = Config.from_env()
    cats = Counter(r.get("category") or "other" for r in rows)
    summary = {
        "total_sites": len(rows),
        "by_category": dict(cats.most_common()),
        "top_plugins": dict(plugins.most_common(10)),
        "top_themes": dict(themes.most_common(5)),
    }
    prompt = (
        "Write a 3-paragraph plain-prose findings summary for a research report on "
        "Zimbabwean websites running WordPress. Use the data below. No markdown headers, "
        "no bullet lists — flowing prose only. Keep it factual; do not speculate beyond the data.\n\n"
        f"Data: {summary}\n"
    )
    try:
        result = run_claude(prompt, model=cfg.anthropic_model, timeout=120)
        return result.text
    except ClaudeError as exc:
        print(f"[report] prose pass failed: {exc}", file=sys.stderr)
        return None


def _write_csv(rows: list[dict]) -> None:
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "domain", "score", "tranco_rank", "category", "sector_tags",
            "theme", "plugins", "homepage_kb", "verified_at", "screenshot",
        ])
        for r in sorted(rows, key=lambda x: (x.get("tranco_rank") or 10**9)):
            w.writerow([
                r.get("domain", ""),
                r.get("score") or "",
                r.get("tranco_rank") or "",
                r.get("category") or "",
                ";".join(r.get("sector_tags") or []),
                r.get("theme") or "",
                ";".join(r.get("plugins") or []),
                r.get("homepage_kb") or "",
                r.get("verified_at") or "",
                r.get("screenshot") or "",
            ])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prose", action="store_true",
                   help="Use claude -p to write a prose findings section (Max sub).")
    p.add_argument("--top-per-category", type=int, default=10)
    args = p.parse_args()
    rows = _load_joined()
    md = _build_report(rows, args.prose, args.top_per_category)
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(md, encoding="utf-8")
    _write_csv(rows)
    print(f"[report] wrote {REPORT_MD} ({len(rows)} sites) and {REPORT_CSV}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
