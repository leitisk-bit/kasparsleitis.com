#!/usr/bin/env bash
# Indoor O Replay — install on Ubuntu 22.04/24.04
# First install:  sudo ADMIN_KEY='your-admin-password' bash install.sh
# Update later:   sudo bash install.sh        (keeps the data and the admin password)
# Installs to /opt/oreplay and runs on 127.0.0.1:8090.
set -euo pipefail
DEST=/opt/oreplay
UNITF=/etc/systemd/system/oreplay.service
if [ -z "${ADMIN_KEY:-}" ] && [ -f "$UNITF" ]; then
  ADMIN_KEY=$(sed -n 's/^Environment=OREPLAY_ADMIN_KEY=//p' "$UNITF")
fi
if [ -z "${ADMIN_KEY:-}" ]; then
  echo "Set an admin password: sudo ADMIN_KEY='your-admin-password' bash install.sh" >&2; exit 1
fi
SRC="$(cd "$(dirname "$0")" && pwd)"
apt-get update
apt-get install -y python3-venv python3-pip poppler-utils fonts-dejavu-core fonts-liberation
mkdir -p "$DEST"
cp -r "$SRC"/app.py "$SRC"/oreplay "$SRC"/static "$SRC"/requirements.txt "$DEST"/
mkdir -p "$DEST"/data
id -u oreplay >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin oreplay
chown -R oreplay:oreplay "$DEST"
python3 -m venv "$DEST"/venv
"$DEST"/venv/bin/pip install --upgrade pip
"$DEST"/venv/bin/pip install -r "$DEST"/requirements.txt
cat > "$UNITF" <<UNIT
[Unit]
Description=Indoor O Replay
After=network.target

[Service]
User=oreplay
WorkingDirectory=$DEST
Environment=OREPLAY_DATA=$DEST/data
Environment=OREPLAY_ADMIN_KEY=$ADMIN_KEY
ExecStart=$DEST/venv/bin/gunicorn -w 1 --threads 8 --timeout 600 -b 127.0.0.1:8090 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
chmod 600 "$UNITF"
systemctl enable oreplay
systemctl restart oreplay
echo
echo "Running on http://127.0.0.1:8090  (check: curl -s localhost:8090/api/maps)"
echo "Put it behind nginx: see nginx-oreplay.conf"
