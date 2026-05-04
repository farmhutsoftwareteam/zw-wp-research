# Sharing this preliminary dataset

The static site at `reports/site/` is fully self-contained — drop it on any
host and it works. This guide covers two paths:

1. **Public GitHub + Vercel auto-deploy** — recommended for "share with a
   friend who has a browser". Free, no setup beyond clicking through Vercel.
2. **Private share** — zip the `reports/site/` directory and email/Drive it.

---

## Path 1 — Public GitHub + Vercel (15 minutes, free, custom URL)

### What gets shared

The `.gitignore` is set up so that **only the publishable artifacts** end up
in git:

- ✅ All pipeline code (`scripts/`, `Makefile`, `requirements.txt`, etc.)
- ✅ `reports/site/` — the static directory (HTML, CSS, JS, `data.json`)
- ✅ `reports/screenshots/*.png` — the 194 verified-site screenshots
- ✅ `reports/zwwp.db` — the SQLite DB (so anyone can query the dataset)
- ✅ `reports/report.md`, `reports/top_zw_wordpress.csv` — the human reports
- ❌ `data/*.jsonl` — raw pipeline JSONL (big, regenerable; **not** shipped)
- ❌ `.env`, `logs/`, screenshots cache, etc. — never shipped

### One-time setup

```bash
# 1. Sanity check — what's about to be tracked
git status
git diff --stat

# 2. Stage and commit (everything except data/*.jsonl is OK)
git add .gitignore vercel.json SHARING.md
git add Makefile README.md requirements.txt .env.example
git add scripts/
git add reports/report.md reports/top_zw_wordpress.csv reports/zwwp.db
git add reports/site/ reports/screenshots/
git status                # confirm — should show NO data/*.jsonl

git commit -m "wire up pipeline, panel fingerprinting, and shareable static site"

# 3. Create the GitHub repo (uses gh CLI, public)
gh repo create zw-wp-research --public --source=. --remote=origin --push

#    Or, if you prefer doing it in the browser:
#    a. https://github.com/new -> name: zw-wp-research
#    b. Don't init with README/license (we already have them)
#    c. git remote add origin git@github.com:<you>/zw-wp-research.git
#    d. git branch -M main
#    e. git push -u origin main
```

### Deploy to Vercel

1. Go to <https://vercel.com/new>
2. Click **Import Git Repository** → select `zw-wp-research`
3. **Project Name**: `zw-wp-research` (gives URL `zw-wp-research.vercel.app`)
4. **Framework Preset**: leave as **Other**
5. **Root Directory**: leave at repo root (`vercel.json` handles the rest)
6. Click **Deploy**

That's it. ~30s later you'll have a live URL like
`https://zw-wp-research.vercel.app` to send your friend.

### Updating the live site

After re-running the pipeline:

```bash
make all                  # or just make index && make site
git add reports/zwwp.db reports/site/ reports/screenshots/
git commit -m "refresh dataset $(date -u +%Y-%m-%d)"
git push
```

Vercel auto-deploys on push. Takes ~30s.

---

## Path 2 — Private share (no GitHub, no Vercel)

```bash
cd reports
zip -r zw-wp-research-site-$(date +%Y%m%d).zip site/ screenshots/ zwwp.db
```

Send the zip via email / Google Drive / WeTransfer. The recipient unzips and
opens `site/index.html` in their browser — works fully offline (the DB isn't
needed; the static site is self-sufficient).

For password-protected hosting, either use Vercel **Pro** ($20/mo) password
protection, or upload the zip to a private Google Drive folder and share by
link.

---

## Notes & disclaimers worth keeping in mind

- **Data is research-grade.** False positives and false negatives both
  exist. The detector relies on rendered HTML; sites with heavy custom
  templates may be miscategorized.
- **Screenshots are from May 2026.** Sites may have changed since.
- **`.gov.zw` and small-business sites** — if a site owner reaches out to be
  removed from the list, just delete that domain's row from the SQLite,
  re-run `make site`, and push. That's the takedown procedure.
- **No PII.** The dataset only contains domain names, public homepage
  metadata, and detected plugin/theme names — all already public via
  inspecting the sites directly.
- **Vercel free tier** is fine for this scale (~10MB site + 100MB
  screenshots; well under any free-tier ceilings).

---

## Tearing it down

If you want the site offline later:

- **Vercel**: project → Settings → "Delete Project"
- **GitHub**: repo → Settings → "Delete this repository"
- **Local**: `make stop && make live-stop && make clean`
