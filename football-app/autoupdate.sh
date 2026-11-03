#!/usr/bin/env bash
# Keeps /opt/football in step with football-app/ on GitHub. Run by cron every 5 minutes:
#   */5 * * * * ~/kasparsleitis.com/football-app/autoupdate.sh >> ~/football-update.log 2>&1
# It only *fetches*: the checkout itself is left to the replay app's updater (telpuo-app/autoupdate.sh),
# which decides whether to rebuild by comparing the checkout with GitHub.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP=${FOOTBALL_DIR:-/opt/football}
cd "$REPO"
exec 9>/tmp/telpuo-update.lock; flock -w 120 9 || exit 0      # same lock as the replay app's updater
git fetch -q origin main
flock -u 9

tree=$(git rev-parse origin/main:football-app)
[ "$(cat "$APP/.deployed" 2>/dev/null)" = "$tree" ] && exit 0

# never swap code under a running data update or model job; try again in 5 minutes
if pgrep -f "$APP/(sync|model)\.py|python (sync|model)\.py" >/dev/null; then
  echo "$(date '+%F %T') new version waiting: a data/model job is running"; exit 0
fi

echo "$(date '+%F %T') updating football to $(git rev-parse --short origin/main)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git archive origin/main football-app | tar -x -C "$tmp"
src="$tmp/football-app"

cp "$src"/*.py "$APP"/
mkdir -p "$APP/static" "$APP/deploy"
cp -r "$src"/static/. "$APP/static/"
cp -r "$src"/deploy/. "$APP/deploy/"
cp "$src"/.env.example "$src"/README.md "$src"/autoupdate.sh "$APP"/ 2>/dev/null || true
if ! cmp -s "$src/requirements.txt" "$APP/requirements.txt"; then
  cp "$src/requirements.txt" "$APP/"
  "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
  echo "  packages updated"
fi
# timer/service files changed? needs root once; tell instead of failing
for f in football-web.service football-sync.service football-sync.timer; do
  if ! cmp -s <(sed "s/REPLACE_USER/$(id -un)/" "$src/deploy/$f") "/etc/systemd/system/$f"; then
    echo "  NOTE: deploy/$f changed — run: cd $APP && sudo bash deploy/install-units.sh"
  fi
done
sudo -n systemctl restart football-web && echo "  web app restarted" || echo "  could not restart (sudoers line missing?)"
echo "$tree" > "$APP/.deployed"
echo "$(date '+%F %T') done"
