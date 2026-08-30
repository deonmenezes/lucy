#!/bin/bash
# Regenerate the journal from Lucy's memory and push it live, but only when
# something actually changed. Run on a timer by ai.lucy.journal.plist.
set -euo pipefail

# launchd gives a minimal PATH, so name the tools explicitly.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")"

BEFORE=""
[ -f site/journal/index.html ] && BEFORE=$(shasum site/journal/index.html | cut -d' ' -f1)

JOURNAL_KEYS="${JOURNAL_KEYS:-local-test}" /opt/homebrew/bin/uv run python journal.py >/dev/null

AFTER=$(shasum site/journal/index.html | cut -d' ' -f1)

if [ "$BEFORE" = "$AFTER" ]; then
  echo "$(date '+%F %T') no change"
  exit 0
fi

echo "$(date '+%F %T') journal changed, deploying"
cd site
/opt/homebrew/bin/vercel deploy --prod --yes --scope deonmenezes-projects >/dev/null 2>&1
echo "$(date '+%F %T') deployed"
