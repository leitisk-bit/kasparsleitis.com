# Indoor O Replay

Map library and route replay for indoor orienteering (telpu orientēšanās).

Two kinds of users:

- **Admin** (signs in with the admin password): uploads course maps (PDF), checks how the map was read, and imports the race results with split times (lof.lv **Split** page saved as PDF or web page).
- **Everyone else**: opens a map, adds their **TCX** watch file and chooses whose run it is. The server checks that the file fits: it must be from the race day and the watch must have run for the whole race (at most 30 s shorter or 10 min longer than the result). Runners whose result time is closest are offered first. If that person already has a run on the map, the new one replaces it. All saved runs are open for everyone to replay, compare, and view in 3D.

How it works:

- **Maps:** the server reads floors, stairwell letters, stair flights (red = up, blue = down), controls in course order, one-way arrows, four-way crossings and tape, then builds a route network that never crosses walls or tape. It also lines the floors up by their stairwells for the 3D view.
- **Runs:** the route of each leg is worked out from the split times and the watch altitude (floor changes show clearly). If the watch was started before the start, the start is found in the recording automatically.
- **Replay:** one run with its fastest-option ghost, or several runs compared; switch between the map and 3D.

## Install on the home server (Docker, next to the tennis app)

```bash
cd ~ && git clone https://github.com/leitisk-bit/kasparsleitis.com.git
cd ~/kasparsleitis.com/telpuo-app
cp .env.example .env
nano .env                       # set ADMIN_KEY to your admin password
docker compose up -d --build    # first build takes a few minutes
```

Open **http://192.168.2.115:8092** on the home network. The tennis app keeps port 8090; this one uses 8092 (change with `WEB_PORT=` in `.env`).
Maps and runs live in the Docker volume `telpuo_data`, so they survive updates.

**Update after code changes:**

```bash
cd ~/kasparsleitis.com && git pull && cd telpuo-app && docker compose up -d --build
```

**Logs:** `docker compose logs -f web`

**Public address (Cloudflare Tunnel):** put the tunnel token in `.env` as `TUNNEL_TOKEN`, then run
`docker compose --profile public up -d --build`. In Cloudflare, the tunnel's public hostname
`telpuo.kasparsleitis.com` points to the service `http://web:8000`. No router ports are opened.
Updates then use `docker compose --profile public up -d --build`.

## Server Info page

**https://kasparsleitis.com/serverinfo** (page in `site/serverinfo/`, data from this app's `/api/server`) shows the server's CPU, memory, disk,
load, network, top apps, Docker containers and Uptime Kuma monitors. It needs the admin password (the same `ADMIN_KEY`);
it never opens without one.

The app reads Netdata from inside Docker, so Netdata must accept connections from Docker but nobody else. Once, on the server:

```bash
sudo sed -i '/^[[:space:]]*bind to[[:space:]]*=/d; /^[[:space:]]*allow connections from[[:space:]]*=/d' /etc/netdata/netdata.conf
sudo sed -i '/^\[web\]/a\    bind to = *\n    allow connections from = localhost 172.*' /etc/netdata/netdata.conf
sudo systemctl restart netdata
sudo ufw status | grep -q "Status: active" && sudo ufw allow from 172.16.0.0/12 to any port 19999,3001 proto tcp
```

Uptime monitors (optional): in Uptime Kuma make a **Status Page** (e.g. slug `server`), add your monitors to it,
then put `KUMA_STATUS_SLUG=server` in `.env` and run the update command.

`install.sh` and `nginx-oreplay.conf` are an alternative for a server without Docker.

## Timing

- Reading a map and building its network takes about 1 minute (A3 map, 4–6 floors).
- Working out one run takes 1–2 minutes.

Both run in the background, and the page updates when they finish.

## Run without installing (testing)

```bash
pip install -r requirements.txt          # plus: sudo apt install poppler-utils
python3 app.py                           # http://127.0.0.1:8090
```

## Files

- `oreplay/analyze.py`: reads the PDF (vector course, letters, digits, chevrons, crossing sign)
- `oreplay/walk.py`: walkable pixels, stair flights, route network
- `oreplay/recon.py`: split parsing, TCX (also GPX/FIT), floor heights, route reconstruction
- `oreplay/results.py`: reads lof.lv split results (groups, runners, clubs, leg times)
- `app.py`: web server and API
- `static/`: library, check screen, replay
