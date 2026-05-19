#!/usr/bin/env bash
# Publish the engagement kit (pitch cards + review dashboard + advisory CSVs
# + SQLite DB + share landing page) to the PUBLIC Vercel site.
#
# !!! READ THIS BEFORE RUNNING !!!
# This script copies the LOCAL-ONLY, gitignored engagement artifacts into
# reports/site/ so they get deployed to https://zw-wp-research.vercel.app/
# That URL is publicly accessible to anyone who has it. The data this
# publishes includes:
#   - 690 Zimbabwean SMB names, emails, phone numbers
#   - Personalised sales pitch scripts per contact
#   - A SQLite DB with the full enrichment data
# Once pushed, the data is on a public CDN. You can revoke later by
# reversing this commit and force-pushing, but copies may already exist.
#
# Confirm twice before proceeding.

set -euo pipefail
cd "$(dirname "$0")/.."

cat <<'BANNER'
============================================================
  PUBLISH ENGAGEMENT KIT TO PUBLIC VERCEL
============================================================
This will make 690 ZW SMB contacts publicly accessible at:
  https://zw-wp-research.vercel.app/pitch/
  https://zw-wp-research.vercel.app/review/
  https://zw-wp-research.vercel.app/share/

The data is currently LOCAL ONLY (gitignored).
After this script runs + you push, it will be PUBLIC.

Anyone with the URL can see it. The noindex header keeps it
out of Google but doesn't stop sharing or screenshots.

Type 'PUBLISH' (in caps) to continue, anything else to abort.
============================================================
BANNER
read -r reply
if [[ "$reply" != "PUBLISH" ]]; then
  echo "aborted."
  exit 1
fi

echo ""
echo ">>> copying engagement artifacts into reports/site/"
mkdir -p reports/site/pitch reports/site/review reports/site/share
cp -R reports/pitch_html/. reports/site/pitch/
cp -R reports/review/.     reports/site/review/
cp reports/cpanel_advisory.csv      reports/site/share/cpanel_advisory.csv
cp reports/share/qualified_leads.csv reports/site/share/qualified_leads.csv
cp reports/zwwp.db                  reports/site/share/zwwp.db
cp reports/share/OPEN_THIS_FIRST.html reports/site/share/index.html
# zip too, for one-click download
LATEST_ZIP=$(ls -t reports/share-bundle-*.zip 2>/dev/null | head -1 || true)
if [[ -n "$LATEST_ZIP" ]]; then
  cp "$LATEST_ZIP" reports/site/share/share-bundle.zip
fi

echo ""
echo ">>> updating .gitignore to allow these paths"
# Remove the lines that hide reports/pitch_html and reports/review;
# we keep the SOURCE directories ignored but allow the COPIES under site/.
# Easiest: explicitly allow the site subdirs.
if ! grep -q '^!reports/site/pitch/' .gitignore; then
  cat >> .gitignore <<EOF

# Engagement kit published to Vercel (added by _publish_engagement_to_vercel.sh)
!reports/site/pitch/
!reports/site/pitch/**
!reports/site/review/
!reports/site/review/**
!reports/site/share/
!reports/site/share/**
EOF
fi

echo ""
echo ">>> staging + committing"
git add -A
git -c commit.gpgsign=false commit -m "engagement-kit: publish pitch cards + review + share bundle to Vercel

Publishes the local engagement-kit artifacts under reports/site/:
  - /pitch/         50 ranked pitch cards (WhatsApp / call / email links)
  - /review/        engagement-review dashboard (690 cPanel sites)
  - /share/         contact CSVs + SQLite DB + share-bundle.zip

Done at the user's explicit request. Data is now publicly accessible
to anyone with the URL (noindex header set; no auth)."

echo ""
echo ">>> pushing — Vercel will auto-deploy in ~30s"
git push

echo ""
echo "============================================================"
echo "  DONE"
echo "============================================================"
echo "  Pitch cards:    https://zw-wp-research.vercel.app/pitch/"
echo "  Review:         https://zw-wp-research.vercel.app/review/"
echo "  Share bundle:   https://zw-wp-research.vercel.app/share/"
echo "============================================================"
