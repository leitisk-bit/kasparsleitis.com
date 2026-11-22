#!/usr/bin/env bash
# Keeps the Telpu O app in step with GitHub. Run by cron every 5 minutes:
#   */5 * * * * ~/kasparsleitis.com/telpuo-app/autoupdate.sh >> ~/telpuo-update.log 2>&1
# Only rebuilds when something in telpuo-app/ changed on GitHub.
# Each run also stays for about 4 1/2 minutes watching for the "Update now" button on the Cilpa page
# (the app leaves a file in telpuo-app/update/, which is shared with its container), so a manual update
# starts within about 10 seconds. What it is doing is written to telpuo-app/update/status.json for the page.
set -uo pipefail
cd "$(dirname "$0")/.."
exec 9>/tmp/telpuo-update.lock; flock -n 9 || exit 0      # never two updates at once
UPD="$PWD/telpuo-app/update"; mkdir -p "$UPD"
LOG="$UPD/last.log"

status() {   # state, message
  python3 - "$1" "$2" "$UPD" <<'PY' 2>/dev/null || true
import json, os, subprocess, sys, time
state, msg, upd = sys.argv[1:4]
g = lambda *a: subprocess.run(['git', *a], capture_output=True, text=True).stdout.strip()
try: log = open(os.path.join(upd, 'last.log'), encoding='utf-8', errors='replace').read().splitlines()[-25:]
except Exception: log = []
old = {}
try: old = json.load(open(os.path.join(upd, 'status.json')))
except Exception: pass
d = {'state': state, 'message': msg, 'checked': time.time(),
     'here': g('log', '-1', '--format=%h', 'HEAD', '--', 'telpuo-app', ':!telpuo-app/VERSION'),
     'github': g('log', '-1', '--format=%h', 'origin/main', '--', 'telpuo-app', ':!telpuo-app/VERSION'),
     'github_change': g('log', '-1', '--format=%s', 'origin/main', '--', 'telpuo-app', ':!telpuo-app/VERSION'),
     'last_update': old.get('last_update'), 'log': log}
if state == 'done': d['last_update'] = time.time()
tmp = os.path.join(upd, 'status.json.tmp'); json.dump(d, open(tmp, 'w'), ensure_ascii=False); os.replace(tmp, os.path.join(upd, 'status.json'))
PY
}

update() {   # $1 = "manual" to rebuild even when GitHub has nothing new
  status checking "Looking at GitHub…"
  if ! git fetch -q origin main 2>>"$LOG"; then status failed "Could not reach GitHub."; return; fi
  if git diff --quiet HEAD origin/main -- telpuo-app && [ "${1:-}" != manual ]; then status idle "Up to date."; return; fi
  : > "$LOG"
  echo "$(date '+%F %T') updating to $(git rev-parse --short origin/main) ${1:-}" | tee -a "$LOG"
  status updating "Downloading the new version…"
  if ! git merge -q --ff-only origin/main >>"$LOG" 2>&1; then status failed "git could not update (local changes on the server?)."; return; fi
  status updating "Building and restarting the app (a few minutes)…"
  ( cd telpuo-app &&
    docker compose --profile public pull -q tunnel &&
    docker compose --profile public up -d --build --remove-orphans &&
    docker image prune -f >/dev/null ) >>"$LOG" 2>&1
  rc=$?
  echo "$(date '+%F %T') done ($rc)" | tee -a "$LOG"
  if [ $rc -eq 0 ]; then status done "Updated."; else status failed "The build failed — see the log."; fi
}

update
end=$((SECONDS + 270))
while [ $SECONDS -lt $end ]; do
  if [ -f "$UPD/request" ]; then rm -f "$UPD/request"; update manual; fi
  sleep 10
done
