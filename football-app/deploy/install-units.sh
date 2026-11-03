#!/usr/bin/env bash
# Installs / refreshes the systemd units and the self-update cron job. Run once with sudo:
#   cd /opt/football && sudo bash deploy/install-units.sh
set -euo pipefail
APP=/opt/football
RUN_USER=${SUDO_USER:-home}
for f in football-web.service football-sync.service football-sync.timer; do
  sed "s/REPLACE_USER/$RUN_USER/" "$APP/deploy/$f" > "/etc/systemd/system/$f"
done
systemctl daemon-reload
systemctl enable --now football-web.service football-sync.timer
systemctl restart football-web.service football-sync.timer

# let the self-update script restart the web app without a password (only this one command)
echo "$RUN_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart football-web, /bin/systemctl restart football-web" \
  > /etc/sudoers.d/football && chmod 440 /etc/sudoers.d/football && visudo -cf /etc/sudoers.d/football >/dev/null

# self-update every 5 minutes from GitHub (football-app/ in ~/kasparsleitis.com)
HOME_DIR=$(getent passwd "$RUN_USER" | cut -d: -f6)
LINE="*/5 * * * * $HOME_DIR/kasparsleitis.com/football-app/autoupdate.sh >> $HOME_DIR/football-update.log 2>&1"
( crontab -u "$RUN_USER" -l 2>/dev/null | grep -v 'football-app/autoupdate.sh' ; echo "$LINE" ) | crontab -u "$RUN_USER" -
chmod +x "$HOME_DIR/kasparsleitis.com/football-app/autoupdate.sh"
echo "Done: units installed, web app restarts allowed, self-update every 5 minutes."
