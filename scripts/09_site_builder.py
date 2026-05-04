#!/usr/bin/env python3
"""Stage 09 — Static site builder.

Reads reports/zwwp.db (built by stage 08) and emits a self-contained static
website at reports/site/. No framework, no build step — pure HTML + CSS + JS.

Browse with:
    python -m http.server 8000 -d reports/site/
    open http://localhost:8000

Deploys as-is to Cloudflare Pages, GitHub Pages, Netlify, or any static host.
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import reports_dir  # noqa: E402

DEFAULT_DB = reports_dir() / "zwwp.db"
DEFAULT_SITE_DIR = reports_dir() / "site"
# Screenshots now live directly inside the site directory (no symlink) so that
# Vercel/static hosts serve them correctly. Stage 06 writes here directly.
SCREENSHOT_SRC = reports_dir() / "site" / "screenshots"


# -----------------------------
# Templates
# -----------------------------
INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Zimbabwe WordPress sites — directory</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header>
  <div class="wrap">
    <h1>Zimbabwe WordPress directory</h1>
    <p class="subtitle">A research index of Zimbabwean websites running WordPress. Generated {generated_at}. <span class="live-dot" title="Auto-refreshes every 30 seconds while the pipeline is live"></span></p>
  </div>
</header>

<section class="stats wrap">
  <div class="stat"><span class="num">{total}</span><span class="lbl">WP sites</span></div>
  <div class="stat"><span class="num">{verified}</span><span class="lbl">verified</span></div>
  <div class="stat"><span class="num">{categories_count}</span><span class="lbl">categories</span></div>
  <div class="stat"><span class="num">{cpanel_count}</span><span class="lbl">on cPanel</span></div>
  <div class="stat"><span class="num">{top_plugin}</span><span class="lbl">most-used plugin</span></div>
  <div class="stat"><span class="num">{top_theme}</span><span class="lbl">most-used theme</span></div>
</section>

<section class="controls wrap">
  <input id="q" type="search" placeholder="Search domain, theme, category…" autocomplete="off">
  <select id="category">
    <option value="">All categories</option>
    {category_options}
  </select>
  <select id="rank">
    <option value="">Any rank</option>
    <option value="top-1k">Top 1k</option>
    <option value="top-10k">Top 10k</option>
    <option value="top-100k">Top 100k</option>
    <option value="top-1m">Top 1M</option>
    <option value="ranked">Ranked anywhere</option>
    <option value="unranked">Unranked</option>
  </select>
  <select id="panel">
    <option value="">Any host panel</option>
    {panel_options}
  </select>
  <label class="check"><input id="hasshot" type="checkbox"> Has screenshot</label>
  <span id="count" class="count"></span>
</section>

<main class="wrap">
  <table id="grid">
    <thead>
      <tr>
        <th data-key="domain">Domain</th>
        <th data-key="tranco_rank" data-num="1">Rank</th>
        <th data-key="score" data-num="1">Score</th>
        <th data-key="category">Category</th>
        <th data-key="theme">Theme</th>
        <th data-key="plugin_count" data-num="1">Plugins</th>
        <th data-key="host_panel">Panel</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
</main>

<footer class="wrap">
  <p>Source: <code>zw-wp-research</code> pipeline. Data is research-grade — false positives possible.</p>
</footer>

<script src="assets/app.js"></script>
</body>
</html>
"""


DOMAIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{domain} — ZW WP directory</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="../assets/style.css">
</head>
<body>
<header>
  <div class="wrap">
    <p><a href="../index.html">&larr; back to directory</a></p>
    <h1>{domain}</h1>
    <p class="subtitle">{category} · score {score} · rank {rank}</p>
  </div>
</header>

<main class="wrap detail">
  {screenshot_block}

  <section>
    <h2>Overview</h2>
    <table class="kv">
      <tr><th>Domain</th><td><a href="https://{domain}/" rel="noopener noreferrer">{domain}</a></td></tr>
      <tr><th>Category</th><td>{category}</td></tr>
      <tr><th>Sector tags</th><td>{sector_tags}</td></tr>
      <tr><th>Theme</th><td>{theme}</td></tr>
      <tr><th>Tranco rank</th><td>{rank}</td></tr>
      <tr><th>WP detection score</th><td>{score} / 100</td></tr>
      <tr><th>WP version</th><td>{wp_version}</td></tr>
      <tr><th>CDN</th><td>{cdn}</td></tr>
      <tr><th>IP</th><td>{ip}</td></tr>
      <tr><th>Homepage size</th><td>{homepage_kb} KB</td></tr>
      <tr><th>Verified at</th><td>{verified_at}</td></tr>
    </table>
  </section>

  <section>
    <h2>Hosting fingerprint</h2>
    <table class="kv">
      <tr><th>Host panel</th><td>{host_panel_label}</td></tr>
      <tr><th>Server header</th><td><code>{server_header}</code></td></tr>
      <tr><th>TLS cert issuer</th><td><code>{cert_issuer}</code></td></tr>
      <tr><th>Reverse PTR</th><td><code>{reverse_ptr}</code></td></tr>
    </table>
    {panel_evidence_html}
  </section>

  <section>
    <h2>Plugins detected ({plugin_count})</h2>
    {plugins_html}
  </section>

  <section>
    <h2>WordPress signals</h2>
    {signals_html}
  </section>

  <section>
    <h2>Discovery sources</h2>
    {seeds_html}
  </section>
</main>

<footer class="wrap"></footer>
</body>
</html>
"""


STYLE_CSS = """:root {
  --fg: #1a1a1a;
  --muted: #6b6b6b;
  --bg: #fafafa;
  --card: #fff;
  --accent: #21759b; /* WP blue */
  --border: #e5e5e5;
  --hover: #f0f6f9;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 14px;
  color: var(--fg);
  background: var(--bg);
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 0 24px; }
header { background: #fff; border-bottom: 1px solid var(--border); padding: 28px 0 22px; }
header h1 { margin: 0 0 4px; font-size: 22px; font-weight: 600; }
header .subtitle { margin: 0; color: var(--muted); font-size: 13px; }
header a { color: var(--accent); text-decoration: none; }
header a:hover { text-decoration: underline; }

.stats { display: flex; gap: 24px; flex-wrap: wrap; padding-top: 22px; padding-bottom: 8px; }
.stat { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 10px 16px; min-width: 110px; }
.stat .num { display: block; font-size: 20px; font-weight: 600; }
.stat .lbl { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }

.controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding-top: 16px; padding-bottom: 12px; }
.controls input[type="search"], .controls select {
  padding: 7px 10px; border: 1px solid var(--border); border-radius: 5px;
  background: #fff; font-size: 13px;
}
.controls input[type="search"] { flex: 1 1 280px; min-width: 240px; }
.controls .check { font-size: 13px; color: var(--muted); display: flex; gap: 6px; align-items: center; }
.controls .count { margin-left: auto; color: var(--muted); font-size: 13px; }

table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
thead th {
  text-align: left; font-weight: 600; font-size: 12px; text-transform: uppercase;
  letter-spacing: .03em; color: var(--muted); padding: 10px 12px; border-bottom: 1px solid var(--border);
  cursor: pointer; user-select: none; background: #fafafa;
}
thead th[aria-sort="asc"]::after { content: " ↑"; }
thead th[aria-sort="desc"]::after { content: " ↓"; }
tbody tr { cursor: pointer; }
tbody tr:hover { background: var(--hover); }
tbody td { padding: 9px 12px; border-bottom: 1px solid var(--border); }
tbody tr:last-child td { border-bottom: none; }
tbody td.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody a { color: var(--accent); text-decoration: none; }
tbody a:hover { text-decoration: underline; }

.detail section { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 16px 20px; margin-top: 16px; }
.detail h2 { margin-top: 0; font-size: 16px; }
table.kv { box-shadow: none; border: none; }
table.kv th { width: 160px; font-weight: 500; color: var(--muted); padding: 6px 12px 6px 0; background: transparent; text-transform: none; letter-spacing: 0; }
table.kv td { padding: 6px 0; border: none; }
.shot { display: block; max-width: 100%; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 16px; }
.tag-list { display: flex; flex-wrap: wrap; gap: 6px; }
.tag { background: #eef4f8; color: var(--accent); padding: 3px 8px; border-radius: 3px; font-size: 12px; }
.tag-cpanel { background: #fff4e0; color: #a36b00; }
.tag-plesk { background: #e0eaff; color: #1e3a8a; }
.tag-directadmin { background: #e8f5e9; color: #1b5e20; }
.tag-litespeed { background: #f3e5f5; color: #6a1b9a; }
.tag-hestia { background: #ffe0e0; color: #b71c1c; }
.tag-vesta { background: #ffe0e0; color: #b71c1c; }
.tag-webmin { background: #ececec; color: #424242; }
.evidence-list { font-family: ui-monospace, monospace; font-size: 12px; color: var(--muted); padding: 8px 12px; background: #fafafa; border: 1px solid var(--border); border-radius: 4px; margin-top: 10px; }
.evidence-list span { display: inline-block; margin-right: 8px; }
.signal-table { width: 100%; }
.signal-table th, .signal-table td { padding: 5px 10px; border-bottom: 1px solid var(--border); text-align: left; }
.signal-table .yes { color: #1a7f37; font-weight: 600; }

footer { color: var(--muted); font-size: 12px; padding: 28px 0 32px; }
code { background: #f0f0f0; padding: 1px 5px; border-radius: 3px; font-size: 12px; }

.live-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: #1a7f37; margin-left: 6px; vertical-align: middle;
  animation: pulse 1.6s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: .35; transform: scale(.8); }
}
"""


APP_JS = r"""(function () {
  let DATA = [];
  const tbody = document.querySelector('#grid tbody');
  const q = document.getElementById('q');
  const cat = document.getElementById('category');
  const rank = document.getElementById('rank');
  const panel = document.getElementById('panel');
  const hasshot = document.getElementById('hasshot');
  const count = document.getElementById('count');
  const ths = document.querySelectorAll('thead th');
  let sortKey = 'tranco_rank';
  let sortAsc = true;

  function rankBucket(r) {
    if (r == null) return 'unranked';
    if (r <= 1000) return 'top-1k';
    if (r <= 10000) return 'top-10k';
    if (r <= 100000) return 'top-100k';
    return 'top-1m';
  }

  function passes(d) {
    const term = (q.value || '').trim().toLowerCase();
    if (term) {
      const blob = (
        (d.domain || '') + ' ' + (d.category || '') + ' ' +
        (d.theme || '') + ' ' + (d.sector_tags || []).join(' ')
      ).toLowerCase();
      if (!blob.includes(term)) return false;
    }
    if (cat.value && d.category !== cat.value) return false;
    if (rank.value) {
      if (rank.value === 'unranked' && d.tranco_rank != null) return false;
      else if (rank.value === 'ranked' && d.tranco_rank == null) return false;
      else if (rank.value !== 'ranked' && rank.value !== 'unranked') {
        if (rankBucket(d.tranco_rank) !== rank.value && !(rank.value === 'top-10k' && d.tranco_rank <= 10000)
            && !(rank.value === 'top-100k' && d.tranco_rank <= 100000)
            && !(rank.value === 'top-1m' && d.tranco_rank <= 1000000)) {
          // strict bucket OR cumulative — keep cumulative behavior
          if (d.tranco_rank == null) return false;
          if (rank.value === 'top-1k' && d.tranco_rank > 1000) return false;
          if (rank.value === 'top-10k' && d.tranco_rank > 10000) return false;
          if (rank.value === 'top-100k' && d.tranco_rank > 100000) return false;
          if (rank.value === 'top-1m' && d.tranco_rank > 1000000) return false;
        }
      }
    }
    if (panel && panel.value) {
      if (panel.value === '__none__') {
        if (d.host_panel) return false;
      } else if (d.host_panel !== panel.value) {
        return false;
      }
    }
    if (hasshot.checked && !d.screenshot) return false;
    return true;
  }

  function cmp(a, b, key, asc) {
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;  // nulls last
    if (bv == null) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return asc ? av - bv : bv - av;
    return asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
  }

  function escape(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  function render() {
    const filtered = DATA.filter(passes).sort((a, b) => cmp(a, b, sortKey, sortAsc));
    count.textContent = filtered.length + ' of ' + DATA.length;
    let html = '';
    for (const d of filtered) {
      const rankCell = d.tranco_rank == null ? '—' : d.tranco_rank.toLocaleString();
      const detail = 'domain/' + encodeURIComponent(d.domain) + '.html';
      const panelCell = d.host_panel
        ? '<span class="tag tag-' + escape(d.host_panel) + '">' + escape(d.host_panel) + '</span>'
        : '—';
      html += '<tr data-href="' + escape(detail) + '">'
        + '<td><a href="' + escape(detail) + '">' + escape(d.domain) + '</a></td>'
        + '<td class="num">' + rankCell + '</td>'
        + '<td class="num">' + (d.score == null ? '—' : d.score) + '</td>'
        + '<td>' + escape(d.category || '—') + '</td>'
        + '<td>' + escape(d.theme || '—') + '</td>'
        + '<td class="num">' + (d.plugin_count == null ? 0 : d.plugin_count) + '</td>'
        + '<td>' + panelCell + '</td>'
        + '</tr>';
    }
    tbody.innerHTML = html;
    ths.forEach(th => {
      const k = th.getAttribute('data-key');
      th.setAttribute('aria-sort', k === sortKey ? (sortAsc ? 'asc' : 'desc') : 'none');
    });
  }

  ths.forEach(th => {
    th.addEventListener('click', () => {
      const k = th.getAttribute('data-key');
      if (sortKey === k) sortAsc = !sortAsc;
      else { sortKey = k; sortAsc = th.hasAttribute('data-num') ? true : true; }
      render();
    });
  });
  [q, cat, rank, panel, hasshot].forEach(el => el && el.addEventListener('input', render));
  tbody.addEventListener('click', (e) => {
    const tr = e.target.closest('tr');
    if (tr && !e.target.closest('a')) {
      const href = tr.getAttribute('data-href');
      if (href) window.location.href = href;
    }
  });

  fetch('data.json').then(r => r.json()).then(d => { DATA = d; render(); });
})();
"""


# -----------------------------
# DB queries
# -----------------------------
def _q_all_domains(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("""
        SELECT domain, ip, cdn, score, wp_version, homepage_status, homepage_kb,
               tranco_rank, cf_radar_bucket, category, category_confidence,
               theme, plugin_count, screenshot, scheme_used, host_panel,
               server_header, cert_issuer, reverse_ptr, panel_evidence,
               verified_at, classified_at, enriched_at, checked_at, resolved_at
        FROM domains
        ORDER BY (tranco_rank IS NULL), tranco_rank, domain
    """)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _q_panels(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    cur = conn.execute(
        "SELECT host_panel, COUNT(*) FROM domains "
        "WHERE host_panel IS NOT NULL AND host_panel <> '' "
        "GROUP BY host_panel ORDER BY 2 DESC"
    )
    return [(r[0], r[1]) for r in cur.fetchall()]


def _q_no_panel_count(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM domains WHERE host_panel IS NULL OR host_panel = ''")
    return cur.fetchone()[0] or 0


def _q_plugins_for(conn: sqlite3.Connection, domain: str) -> list[str]:
    cur = conn.execute("SELECT plugin FROM plugins WHERE domain = ? ORDER BY plugin", (domain,))
    return [r[0] for r in cur.fetchall()]


def _q_signals_for(conn: sqlite3.Connection, domain: str) -> list[tuple[str, int]]:
    cur = conn.execute("SELECT signal, weight FROM signals WHERE domain = ? ORDER BY weight DESC", (domain,))
    return [(r[0], r[1]) for r in cur.fetchall()]


def _q_tags_for(conn: sqlite3.Connection, domain: str) -> list[str]:
    cur = conn.execute("SELECT tag FROM sector_tags WHERE domain = ? ORDER BY tag", (domain,))
    return [r[0] for r in cur.fetchall()]


def _q_seeds_for(conn: sqlite3.Connection, domain: str) -> list[tuple[str, str]]:
    cur = conn.execute("SELECT source, hint FROM seeds WHERE domain = ? ORDER BY source", (domain,))
    return [(r[0], r[1] or "") for r in cur.fetchall()]


def _q_top_plugin(conn: sqlite3.Connection) -> str:
    cur = conn.execute(
        "SELECT plugin, COUNT(*) AS n FROM plugins GROUP BY plugin ORDER BY n DESC LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else "—"


def _q_top_theme(conn: sqlite3.Connection) -> str:
    cur = conn.execute(
        "SELECT theme, COUNT(*) AS n FROM domains "
        "WHERE theme IS NOT NULL AND theme <> '' GROUP BY theme ORDER BY n DESC LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else "—"


def _q_categories(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    cur = conn.execute(
        "SELECT category, COUNT(*) FROM domains "
        "WHERE category IS NOT NULL AND category <> '' GROUP BY category ORDER BY 2 DESC"
    )
    return [(r[0], r[1]) for r in cur.fetchall()]


def _q_meta(conn: sqlite3.Connection) -> dict:
    out = {}
    for row in conn.execute("SELECT key, value FROM meta"):
        out[row[0]] = row[1]
    return out


# -----------------------------
# Renderers
# -----------------------------
def _render_screenshot(domain: str, screenshot: str | None, site_dir: Path) -> str:
    if not screenshot:
        return ""
    return f'<img class="shot" src="../{html.escape(screenshot)}" alt="Screenshot of {html.escape(domain)}">'


def _render_plugins(plugins: list[str]) -> str:
    if not plugins:
        return "<p>No plugins fingerprinted.</p>"
    items = "".join(f'<span class="tag">{html.escape(p)}</span>' for p in plugins)
    return f'<div class="tag-list">{items}</div>'


def _render_signals(signals: list[tuple[str, int]]) -> str:
    if not signals:
        return "<p>No signals stored.</p>"
    rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td class=\"yes\">+{w}</td></tr>"
        for name, w in signals
    )
    return f'<table class="signal-table"><thead><tr><th>Signal</th><th>Weight</th></tr></thead><tbody>{rows}</tbody></table>'


def _render_seeds(seeds: list[tuple[str, str]]) -> str:
    if not seeds:
        return "<p>No discovery sources recorded.</p>"
    rows = "".join(
        f"<tr><td>{html.escape(s)}</td><td>{html.escape(h)}</td></tr>"
        for s, h in seeds
    )
    return f'<table class="signal-table"><thead><tr><th>Source</th><th>Hint</th></tr></thead><tbody>{rows}</tbody></table>'


def _render_tags(tags: list[str]) -> str:
    if not tags:
        return "—"
    return " ".join(f'<span class="tag">{html.escape(t)}</span>' for t in tags)


def _render_panel_evidence(evidence_json: str | None) -> str:
    if not evidence_json:
        return ""
    try:
        ev = json.loads(evidence_json)
    except Exception:
        return ""
    matches = ev.get("matches") or []
    if not matches:
        return '<p class="evidence-list">no positive panel signals</p>'
    chips = "".join(f'<span>{html.escape(m)}</span>' for m in matches)
    return f'<div class="evidence-list"><strong>Evidence:</strong> {chips}</div>'


# -----------------------------
# Build
# -----------------------------
def build(db_path: Path, site_dir: Path, top_n: int | None) -> dict[str, int]:
    if not db_path.exists():
        print(f"[site] db not found: {db_path}; run stage 08 first", file=sys.stderr)
        return {"pages": 0}
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "assets").mkdir(parents=True, exist_ok=True)
    (site_dir / "domain").mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    domains = _q_all_domains(conn)
    if top_n:
        domains = domains[:top_n]

    # Augment with sector_tags & plugins (small joins to keep data.json lean)
    for d in domains:
        d["sector_tags"] = _q_tags_for(conn, d["domain"])
        d["plugins"] = _q_plugins_for(conn, d["domain"])

    # data.json — slimmed for client-side filtering
    slim = [
        {
            "domain": d["domain"],
            "tranco_rank": d.get("tranco_rank"),
            "score": d.get("score"),
            "category": d.get("category"),
            "theme": d.get("theme"),
            "plugin_count": d.get("plugin_count") or 0,
            "screenshot": d.get("screenshot"),
            "sector_tags": d.get("sector_tags") or [],
            "host_panel": d.get("host_panel"),
        }
        for d in domains
    ]
    (site_dir / "data.json").write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")

    # assets
    (site_dir / "assets" / "style.css").write_text(STYLE_CSS, encoding="utf-8")
    (site_dir / "assets" / "app.js").write_text(APP_JS, encoding="utf-8")

    # Screenshots already live at reports/site/screenshots/ (stage 06 writes
    # there directly), so no copy or symlink is needed.

    # index.html
    meta = _q_meta(conn)
    cats = _q_categories(conn)
    cat_options = "".join(
        f'<option value="{html.escape(c)}">{html.escape(c.title())} ({n})</option>'
        for c, n in cats
    )
    panels_breakdown = _q_panels(conn)
    no_panel = _q_no_panel_count(conn)
    panel_options = "".join(
        f'<option value="{html.escape(p)}">{html.escape(p)} ({n})</option>'
        for p, n in panels_breakdown
    )
    if no_panel:
        panel_options += f'<option value="__none__">none / unknown ({no_panel})</option>'
    cpanel_count = next((n for p, n in panels_breakdown if p == "cpanel"), 0)
    index_html = INDEX_HTML.format(
        generated_at=meta.get("generated_at", "—"),
        total=len(domains),
        verified=sum(1 for d in domains if d.get("verified_at")),
        categories_count=len(cats),
        cpanel_count=cpanel_count,
        top_plugin=html.escape(_q_top_plugin(conn)),
        top_theme=html.escape(_q_top_theme(conn)),
        category_options=cat_options,
        panel_options=panel_options,
    )
    (site_dir / "index.html").write_text(index_html, encoding="utf-8")

    pages = 0
    for d in domains:
        seeds = _q_seeds_for(conn, d["domain"])
        signals = _q_signals_for(conn, d["domain"])
        screenshot_block = _render_screenshot(d["domain"], d.get("screenshot"), site_dir)
        panel_label = d.get("host_panel") or "unknown"
        host_panel_label = (
            f'<span class="tag tag-{html.escape(panel_label)}">{html.escape(panel_label)}</span>'
        )
        evidence_html = _render_panel_evidence(d.get("panel_evidence"))
        page = DOMAIN_HTML.format(
            domain=html.escape(d["domain"]),
            category=html.escape(d.get("category") or "uncategorized"),
            sector_tags=_render_tags(d.get("sector_tags") or []),
            theme=html.escape(d.get("theme") or "—"),
            rank=str(d.get("tranco_rank") or "—"),
            score=str(d.get("score") or "—"),
            wp_version=html.escape(d.get("wp_version") or "—"),
            cdn=html.escape(d.get("cdn") or "—"),
            ip=html.escape(d.get("ip") or "—"),
            homepage_kb=str(d.get("homepage_kb") or "—"),
            verified_at=html.escape(d.get("verified_at") or "—"),
            plugin_count=len(d.get("plugins") or []),
            plugins_html=_render_plugins(d.get("plugins") or []),
            signals_html=_render_signals(signals),
            seeds_html=_render_seeds(seeds),
            screenshot_block=screenshot_block,
            host_panel_label=host_panel_label,
            server_header=html.escape((d.get("server_header") or "—")[:200]),
            cert_issuer=html.escape((d.get("cert_issuer") or "—")[:200]),
            reverse_ptr=html.escape((d.get("reverse_ptr") or "—")[:200]),
            panel_evidence_html=evidence_html,
        )
        (site_dir / "domain" / f"{d['domain']}.html").write_text(page, encoding="utf-8")
        pages += 1

    conn.close()
    return {"pages": pages, "index": 1}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(DEFAULT_DB))
    p.add_argument("--site-dir", default=str(DEFAULT_SITE_DIR))
    p.add_argument("--top-n", type=int, default=None)
    args = p.parse_args()
    counts = build(Path(args.db_path), Path(args.site_dir), args.top_n)
    print(f"[site] built {counts['pages']} domain pages + index at {args.site_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
