#!/usr/bin/env python3
"""Stage 13 — Local engagement-review dashboard.

Joins cpanel_advisory.csv + cpanel_version_audit.csv (and the SQLite for any
gaps) and emits a SINGLE self-contained HTML file at
`reports/review/index.html` plus its data file `data.json`. Open it locally
with `make review` or directly:

    open reports/review/index.html

This is intentionally separate from the public site at `reports/site/` —
**it is not pushed to Vercel**. The review dashboard contains contact
emails, phone numbers, and an outreach-selection workflow that should stay
internal to the engagement team.

Features in the dashboard:
  - Summary stat strip
  - Search box (domain / category / theme / email / theme)
  - Filters: category, host_panel, http_server, "outdated WP only",
            "has email", "has phone"
  - Sortable columns
  - Per-row "select for outreach" checkbox
  - "Export selected as CSV" + "Export selected as .eml drafts JSON"
  - Per-row link to the public detail page (so you can spot-check what we
    show vs. what the site actually looks like)
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import reports_dir  # noqa: E402

DB_PATH = reports_dir() / "zwwp.db"
ADVISORY_CSV = reports_dir() / "cpanel_advisory.csv"
VERSION_CSV = reports_dir() / "cpanel_version_audit.csv"
COMPROMISE_PATH = reports_dir().parent / "data" / "compromise_check.jsonl"
OUT_DIR = reports_dir() / "review"


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_compromise() -> dict[str, dict]:
    if not COMPROMISE_PATH.exists():
        return {}
    out: dict[str, dict] = {}
    with COMPROMISE_PATH.open("rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            d = rec.get("domain")
            if d:
                out[d] = rec
    return out


def _load_db_overlay() -> dict[str, dict]:
    """Pull a few extra fields from SQLite (Tranco rank, ip, cdn) in case
    the CSVs are missing them for some rows."""
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        SELECT domain, tranco_rank, score, ip, cdn, theme, plugin_count, screenshot
        FROM domains
        WHERE host_panel='cpanel' AND score>=70
    """)
    cols = [c[0] for c in cur.description]
    out = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}
    conn.close()
    return out


def build() -> int:
    advisory = _load_csv(ADVISORY_CSV)
    version = _load_csv(VERSION_CSV)
    compromise = _load_compromise()
    overlay = _load_db_overlay()

    # Engagement scope = the cPanel sites only. Build by_domain from advisory
    # (which is already cPanel-filtered) and merge version+overlay onto those.
    by_domain: dict[str, dict] = {}
    for r in advisory:
        d = r.get("domain")
        if d:
            by_domain[d] = {**r}
    version_index = {r.get("domain"): r for r in version if r.get("domain")}
    for d, rec in by_domain.items():
        v = version_index.get(d) or {}
        for k, val in v.items():
            if k != "domain" and val not in (None, "") and rec.get(k) in (None, ""):
                rec[k] = val
        ov = overlay.get(d) or {}
        for k in ("tranco_rank", "score", "ip", "cdn", "theme", "plugin_count", "screenshot"):
            if rec.get(k) in (None, "") and ov.get(k) is not None:
                rec[k] = ov[k]
        comp = compromise.get(d)
        if comp:
            rec["compromised"] = comp.get("compromised", False)
            rec["indicator_count"] = comp.get("indicator_count", 0)
            rec["indicator_paths"] = ";".join(
                i.get("path", "") for i in (comp.get("indicators") or [])
            )

    rows = list(by_domain.values())
    rows.sort(key=lambda r: (
        not bool(r.get("compromised")),  # compromised first
        not bool(r.get("outdated_flags")),  # outdated next
        int(r.get("tranco_rank") or 10**9),
        r.get("domain") or "",
    ))

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load suppressed domains
    suppressed_domains: set[str] = set()
    try:
        import sqlite3 as _sq
        sc = _sq.connect(DB_PATH)
        sc.row_factory = _sq.Row
        for sr in sc.execute("SELECT domain FROM suppressions WHERE domain IS NOT NULL"):
            suppressed_domains.add(sr["domain"])
        sc.close()
    except Exception:
        pass

    # data.json — slim payload for client-side filtering
    slim = []
    for r in rows:
        domain = r.get("domain")
        slim.append({
            "domain": domain,
            "tranco_rank": int(r["tranco_rank"]) if (r.get("tranco_rank") and str(r["tranco_rank"]).isdigit()) else None,
            "category": r.get("category") or "",
            "theme": r.get("theme") or "",
            "ip": r.get("ip") or "",
            "cdn": r.get("cdn") or "",
            "host_panel": r.get("host_panel") or "cpanel",
            "http_server": r.get("http_server") or "",
            "http_version": r.get("http_version") or "",
            "wp_version": r.get("wp_version") or "",
            "outdated_flags": r.get("outdated_flags") or "",
            "email": r.get("primary_contact_email") or "",
            "all_emails": r.get("all_emails") or "",
            "phones": r.get("phones") or "",
            "socials": r.get("socials") or "",
            "addresses": r.get("addresses") or "",
            "screenshot": r.get("screenshot") or "",
            "compromised": bool(r.get("compromised")),
            "indicator_paths": r.get("indicator_paths") or "",
            "suppressed": domain in suppressed_domains,
        })
    (OUT_DIR / "data.json").write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")

    # stats
    cats = Counter(r["category"] for r in slim if r["category"])
    http_servers = Counter(r["http_server"] for r in slim if r["http_server"])
    has_email = sum(1 for r in slim if r["email"])
    has_phone = sum(1 for r in slim if r["phones"])
    has_outdated = sum(1 for r in slim if r["outdated_flags"])
    has_compromised = sum(1 for r in slim if r["compromised"])

    cat_options = "".join(
        f'<option value="{html.escape(c)}">{html.escape(c.title())} ({n})</option>'
        for c, n in cats.most_common()
    )
    server_options = "".join(
        f'<option value="{html.escape(s)}">{html.escape(s)} ({n})</option>'
        for s, n in http_servers.most_common()
    )

    page = INDEX_HTML.format(
        total=len(slim),
        has_email=has_email,
        has_phone=has_phone,
        has_outdated=has_outdated,
        has_compromised=has_compromised,
        cat_options=cat_options,
        server_options=server_options,
    )
    (OUT_DIR / "index.html").write_text(page, encoding="utf-8")
    (OUT_DIR / "review.css").write_text(STYLE_CSS, encoding="utf-8")
    (OUT_DIR / "review.js").write_text(APP_JS, encoding="utf-8")
    print(f"[review] wrote {OUT_DIR}/index.html ({len(slim)} rows)", file=sys.stderr)
    print(f"[review] open with: open {OUT_DIR}/index.html", file=sys.stderr)
    return len(slim)


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>cPanel engagement review (PRIVATE)</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<link rel="stylesheet" href="review.css">
</head>
<body>
<div class="banner">
  <strong>PRIVATE — INTERNAL ENGAGEMENT REVIEW</strong>
  <span>Do not share this URL or screenshot. Contact data on this page is for outreach planning only.</span>
</div>
<header>
  <div class="wrap">
    <h1>cPanel engagement — review &amp; selection</h1>
    <p class="subtitle">CVE-2026-41940 outreach prep for {total} cPanel-positive Zimbabwean WordPress sites.</p>
  </div>
</header>

<section class="stats wrap">
  <div class="stat"><span class="num">{total}</span><span class="lbl">cPanel sites</span></div>
  <div class="stat"><span class="num">{has_email}</span><span class="lbl">with email</span></div>
  <div class="stat"><span class="num">{has_phone}</span><span class="lbl">with phone</span></div>
  <div class="stat warn"><span class="num">{has_outdated}</span><span class="lbl">outdated WP</span></div>
  <div class="stat danger"><span class="num">{has_compromised}</span><span class="lbl">compromise indicators</span></div>
  <div class="stat" id="selectedStat"><span class="num" id="selectedCount">0</span><span class="lbl">selected</span></div>
</section>

<section class="controls wrap">
  <input id="q" type="search" placeholder="Search domain / theme / email / phone…" autocomplete="off">
  <select id="category">
    <option value="">All categories</option>
    {cat_options}
  </select>
  <select id="http_server">
    <option value="">All HTTP servers</option>
    {server_options}
  </select>
  <label class="check"><input id="hasemail" type="checkbox"> Has email</label>
  <label class="check"><input id="hasphone" type="checkbox"> Has phone</label>
  <label class="check"><input id="outdatedonly" type="checkbox"> Outdated only</label>
  <label class="check"><input id="compronly" type="checkbox"> Compromised only</label>
  <label class="check"><input id="hidesuppr" type="checkbox" checked> Hide suppressed</label>
  <span id="count" class="count"></span>
</section>

<section class="actions wrap">
  <button id="selectAll" type="button">Select all visible</button>
  <button id="selectNone" type="button">Clear selection</button>
  <button id="exportCsv" type="button">Export selected → CSV</button>
  <button id="exportJson" type="button">Export selected → JSON (mail-merge)</button>
  <button id="copyEmails" type="button">Copy selected emails to clipboard</button>
</section>

<main class="wrap">
  <table id="grid">
    <thead>
      <tr>
        <th class="check-col"><input id="selAll2" type="checkbox" title="Select all visible"></th>
        <th data-key="domain">Domain</th>
        <th data-key="tranco_rank" data-num="1">Rank</th>
        <th data-key="category">Category</th>
        <th data-key="http_server">Server</th>
        <th data-key="wp_version">WP</th>
        <th data-key="outdated_flags">Flags</th>
        <th data-key="email">Email</th>
        <th data-key="phones">Phone</th>
        <th>Live</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
</main>

<footer class="wrap">
  <p>Sources: cpanel_advisory.csv (contact scrape) · cpanel_version_audit.csv (version analysis) · zwwp.db (SQLite) · compromise_check.jsonl (if present).</p>
</footer>

<script src="review.js"></script>
</body>
</html>
"""


STYLE_CSS = """:root {
  --fg: #1a1a1a;
  --muted: #6b6b6b;
  --bg: #fafafa;
  --card: #fff;
  --border: #e5e5e5;
  --hover: #fff8e1;
  --accent: #c0392b;
  --warn: #d35400;
  --danger: #b71c1c;
  --ok: #1b5e20;
}
* { box-sizing: border-box; }
body { margin:0; font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif; color:var(--fg); background:var(--bg); }
.banner { background:#fff3e0; border-bottom:2px solid #f57c00; padding:8px 24px; font-size:12px; display:flex; gap:14px; align-items:center; }
.banner strong { color:#bf360c; }
.banner span { color:#6d4c41; }
.wrap { max-width:1480px; margin:0 auto; padding:0 24px; }
header { background:#fff; border-bottom:1px solid var(--border); padding:22px 0 18px; }
header h1 { margin:0 0 4px; font-size:20px; font-weight:600; }
header .subtitle { margin:0; color:var(--muted); font-size:12px; }

.stats { display:flex; gap:14px; flex-wrap:wrap; padding-top:18px; padding-bottom:6px; }
.stat { background:var(--card); border:1px solid var(--border); border-radius:6px; padding:8px 14px; min-width:90px; }
.stat .num { display:block; font-size:18px; font-weight:600; }
.stat .lbl { display:block; color:var(--muted); font-size:11px; margin-top:2px; }
.stat.warn .num { color:var(--warn); }
.stat.danger .num { color:var(--danger); }
#selectedStat .num { color:var(--accent); }

.controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:12px 24px; }
.controls input[type=search], .controls select { padding:6px 10px; border:1px solid var(--border); border-radius:5px; background:#fff; font-size:12px; }
.controls input[type=search] { flex:1 1 280px; min-width:220px; }
.controls .check { font-size:12px; color:var(--muted); display:flex; gap:5px; align-items:center; }
.controls .count { margin-left:auto; color:var(--muted); font-size:12px; }

.actions { display:flex; gap:8px; padding:0 24px 12px; flex-wrap:wrap; }
.actions button { padding:6px 12px; border:1px solid var(--border); background:#fff; border-radius:5px; font-size:12px; cursor:pointer; color:var(--fg); }
.actions button:hover { background:#f0f0f0; }
.actions button#exportCsv, .actions button#exportJson { background:#21759b; color:#fff; border-color:#1d6586; }
.actions button#exportCsv:hover, .actions button#exportJson:hover { background:#1d6586; }

table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--border); border-radius:6px; overflow:hidden; }
thead th { text-align:left; font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:.03em; color:var(--muted); padding:8px 10px; border-bottom:1px solid var(--border); cursor:pointer; user-select:none; background:#fafafa; white-space:nowrap; }
thead th[aria-sort=asc]::after { content:" ↑"; }
thead th[aria-sort=desc]::after { content:" ↓"; }
thead th.check-col { width:30px; }
tbody td { padding:7px 10px; border-bottom:1px solid var(--border); vertical-align:top; }
tbody tr:hover { background:var(--hover); }
tbody tr.suppressed { background:#f5f5f5; color:#9e9e9e; text-decoration:line-through; }
tbody tr.suppressed:hover { background:#eeeeee; }
tbody tr.compromised { background:#ffebee; }
tbody tr.outdated { background:#fff8e1; }
tbody tr.compromised:hover { background:#ffcdd2; }
tbody tr.outdated:hover { background:#fff3c4; }
tbody tr.selected { background:#e3f2fd !important; }
tbody td.num { text-align:right; font-variant-numeric:tabular-nums; }
tbody a { color:#1565c0; text-decoration:none; }
tbody a:hover { text-decoration:underline; }
tbody td.flag { color:var(--warn); font-size:11px; }
tbody tr.compromised td.flag { color:var(--danger); font-weight:600; }

footer { color:var(--muted); font-size:11px; padding:18px 0 24px; }
"""


APP_JS = r"""(function(){
  let DATA = [];
  const tbody = document.querySelector('#grid tbody');
  const q = document.getElementById('q');
  const cat = document.getElementById('category');
  const srv = document.getElementById('http_server');
  const hasemail = document.getElementById('hasemail');
  const hasphone = document.getElementById('hasphone');
  const outdatedonly = document.getElementById('outdatedonly');
  const compronly = document.getElementById('compronly');
  const hidesuppr = document.getElementById('hidesuppr');
  const count = document.getElementById('count');
  const selectedCount = document.getElementById('selectedCount');
  const ths = document.querySelectorAll('thead th[data-key]');
  const selAll2 = document.getElementById('selAll2');
  let sortKey = 'tranco_rank';
  let sortAsc = true;
  const selected = new Set();

  function passes(d) {
    const term = (q.value||'').trim().toLowerCase();
    if (term) {
      const blob = (d.domain+' '+d.category+' '+d.theme+' '+d.email+' '+d.phones+' '+d.all_emails).toLowerCase();
      if (!blob.includes(term)) return false;
    }
    if (cat.value && d.category !== cat.value) return false;
    if (srv.value && d.http_server !== srv.value) return false;
    if (hasemail.checked && !d.email) return false;
    if (hasphone.checked && !d.phones) return false;
    if (outdatedonly.checked && !d.outdated_flags) return false;
    if (compronly.checked && !d.compromised) return false;
    if (hidesuppr.checked && d.suppressed) return false;
    return true;
  }

  function cmp(a, b, key, asc) {
    const av = a[key], bv = b[key];
    const na = (av==null||av==='');
    const nb = (bv==null||bv==='');
    if (na && nb) return 0;
    if (na) return 1;
    if (nb) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return asc ? av-bv : bv-av;
    return asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
  }

  function escape(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":'&#39;'}[c]));}

  function render() {
    const filtered = DATA.filter(passes).sort((a,b)=>cmp(a,b,sortKey,sortAsc));
    count.textContent = filtered.length+' of '+DATA.length;
    const liveSiteBase = window.location.protocol==='file:' ? '' : '/';
    const html = filtered.map(d=>{
      const cls = [];
      if (d.suppressed) cls.push('suppressed');
      else if (d.compromised) cls.push('compromised');
      else if (d.outdated_flags) cls.push('outdated');
      if (selected.has(d.domain)) cls.push('selected');
      const checked = selected.has(d.domain) ? 'checked' : '';
      const rank = d.tranco_rank==null ? '—' : d.tranco_rank.toLocaleString();
      const live = `https://zw-wp-research.vercel.app/domain/${encodeURIComponent(d.domain)}.html`;
      return `<tr class="${cls.join(' ')}" data-domain="${escape(d.domain)}">`
        + `<td class="check-col"><input type="checkbox" class="rowcheck" ${checked}></td>`
        + `<td><strong>${escape(d.domain)}</strong></td>`
        + `<td class="num">${rank}</td>`
        + `<td>${escape(d.category||'—')}</td>`
        + `<td>${escape(d.http_server||'—')}${d.http_version?' '+escape(d.http_version):''}</td>`
        + `<td>${escape(d.wp_version||'—')}</td>`
        + `<td class="flag">${escape((d.outdated_flags||'').replace(/;/g,', '))}${d.compromised?'<br><strong>COMPROMISED</strong>':''}</td>`
        + `<td>${escape(d.email||'—')}</td>`
        + `<td>${escape((d.phones||'').split(';')[0]||'—')}</td>`
        + `<td><a href="${live}" target="_blank" rel="noopener">↗</a></td>`
        + '</tr>';
    }).join('');
    tbody.innerHTML = html;
    ths.forEach(th=>{
      const k = th.getAttribute('data-key');
      th.setAttribute('aria-sort', k===sortKey?(sortAsc?'asc':'desc'):'none');
    });
    selectedCount.textContent = selected.size;
  }

  ths.forEach(th=>th.addEventListener('click',()=>{
    const k=th.getAttribute('data-key');
    if (sortKey===k) sortAsc=!sortAsc;
    else { sortKey=k; sortAsc=true; }
    render();
  }));
  [q,cat,srv,hasemail,hasphone,outdatedonly,compronly,hidesuppr].forEach(el=>el.addEventListener('input',render));

  tbody.addEventListener('change', e=>{
    const cb = e.target;
    if (cb.classList.contains('rowcheck')) {
      const d = cb.closest('tr').getAttribute('data-domain');
      if (cb.checked) selected.add(d);
      else selected.delete(d);
      render();
    }
  });

  document.getElementById('selectAll').addEventListener('click',()=>{
    DATA.filter(passes).forEach(d=>selected.add(d.domain));
    render();
  });
  document.getElementById('selectNone').addEventListener('click',()=>{
    selected.clear();
    render();
  });
  selAll2.addEventListener('change',()=>{
    if (selAll2.checked) DATA.filter(passes).forEach(d=>selected.add(d.domain));
    else DATA.filter(passes).forEach(d=>selected.delete(d.domain));
    render();
  });

  function selectedRows(){ return DATA.filter(d=>selected.has(d.domain)); }

  function downloadBlob(content, name, type){
    const blob = new Blob([content], {type});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href=url; a.download=name; document.body.appendChild(a); a.click();
    setTimeout(()=>{URL.revokeObjectURL(url); a.remove();}, 100);
  }

  document.getElementById('exportCsv').addEventListener('click',()=>{
    const rows = selectedRows();
    if (!rows.length) return alert('No rows selected.');
    const cols = ['domain','tranco_rank','category','http_server','http_version','wp_version','outdated_flags','email','all_emails','phones','socials','addresses','compromised','indicator_paths'];
    const csv = [cols.join(',')].concat(
      rows.map(r=>cols.map(c=>{
        let v = r[c]==null?'':String(r[c]);
        if (v.includes(',')||v.includes('"')||v.includes('\n')) v='"'+v.replace(/"/g,'""')+'"';
        return v;
      }).join(','))
    ).join('\n');
    downloadBlob(csv, `outreach_selection_${Date.now()}.csv`, 'text/csv');
  });

  document.getElementById('exportJson').addEventListener('click',()=>{
    const rows = selectedRows();
    if (!rows.length) return alert('No rows selected.');
    downloadBlob(JSON.stringify(rows, null, 2), `outreach_selection_${Date.now()}.json`, 'application/json');
  });

  document.getElementById('copyEmails').addEventListener('click',()=>{
    const rows = selectedRows();
    const emails = rows.map(r=>r.email).filter(Boolean);
    if (!emails.length) return alert('No emails in selection.');
    navigator.clipboard.writeText(emails.join(', ')).then(()=>{
      alert(`Copied ${emails.length} emails to clipboard.`);
    });
  });

  fetch('data.json').then(r=>r.json()).then(d=>{ DATA=d; render(); });
})();
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.parse_args()
    n = build()
    print(f"[review] {n} rows in dashboard", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
