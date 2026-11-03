#!/usr/bin/env bash
# Run on the Ubuntu server from the unpacked project folder:  sudo bash deploy/install.sh
set -euo pipefail
APP=/opt/football
RUN_USER=${SUDO_USER:-$USER}

apt-get update -qq && apt-get install -y -qq python3-venv sqlite3 >/dev/null
mkdir -p "$APP"
cp -r ./*.py ./static ./requirements.txt ./.env.example ./deploy "$APP"/
[ -f "$APP/.env" ] || cp "$APP/.env.example" "$APP/.env"
mkdir -p "$APP/data"
chown -R "$RUN_USER":"$RUN_USER" "$APP"
chmod 600 "$APP/.env"

sudo -u "$RUN_USER" python3 -m venv "$APP/.venv"
sudo -u "$RUN_USER" "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

for f in football-web.service football-sync.service football-sync.timer; do
  sed "s/REPLACE_USER/$RUN_USER/" "$APP/deploy/$f" > "/etc/systemd/system/$f"
done
systemctl daemon-reload
systemctl enable --now football-web.service football-sync.timer

echo
echo "Installed to $APP. Next: edit $APP/.env (API key, admin password, secret), then:"
echo "  sudo systemctl restart football-web"
echo "  curl -s http://127.0.0.1:8095/football/health"
