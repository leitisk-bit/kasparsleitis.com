#!/usr/bin/env bash
# Keeps the Telpu O app in step with GitHub. Run by cron every 5 minutes:
#   */5 * * * * ~/kasparsleitis.com/telpuo-app/autoupdate.sh >> ~/telpuo-update.log 2>&1
# Only rebuilds when something in telpuo-app/ changed on GitHub.
set -euo pipefail
cd "$(dirname "$0")/.."
exec 9>/tmp/telpuo-update.lock; flock -n 9 || exit 0      # never two updates at once
git fetch -q origin main
if git diff --quiet HEAD origin/main -- telpuo-app; then exit 0; fi
echo "$(date '+%F %T') updating to $(git rev-parse --short origin/main)"
git merge -q --ff-only origin/main
cd telpuo-app
docker compose --profile public pull -q tunnel
docker compose --profile public up -d --build --remove-orphans
docker image prune -f >/dev/null
echo "$(date '+%F %T') done"
