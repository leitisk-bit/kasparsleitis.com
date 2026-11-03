# Football stats — kasparsleitis.com/football

Corners, shots, shots on target, offsides and substitutions for the top 5 leagues
plus Champions League, Europa League and Conference League, 2021/22 → today.
Data from API-Football, stored in SQLite on the Ubuntu server. The whole page is behind the admin password.

## 1. Install on the server

```bash
scp football.zip user@server:~ && ssh user@server
unzip football.zip -d football && cd football
sudo bash deploy/install.sh
nano /opt/football/.env      # API key, ADMIN_PASSWORD (same as replay app), SECRET_KEY
sudo systemctl restart football-web
curl -s http://127.0.0.1:8095/football/health   # {"ok":true}
```

**Same admin password as the replay app:** open that app's `.env` / config on the server and copy the value.
If it's stored as plain text → `ADMIN_PASSWORD=...`. If it's a bcrypt hash (`$2b$...`) → `ADMIN_PASSWORD_HASH=...`.

## 2. Make it public at kasparsleitis.com/football

The main site lives on Namecheap, so only the `/football` path should go to the server:

1. **Cloudflare Tunnel:** add a public hostname `football-origin.kasparsleitis.com` → `http://localhost:8095`
   (Zero Trust → Networks → Tunnels → your tunnel → Public Hostname).
2. **Cloudflare Worker:** create a worker with `deploy/cloudflare-worker.js` and add the route
   `kasparsleitis.com/football*` (plus `www.kasparsleitis.com/football*`).

Simpler alternative: use only `football.kasparsleitis.com` as the tunnel hostname, set `BASE_PATH=/football`
and open `https://football.kasparsleitis.com/football/`.

## 3. Load 5 years of data

```bash
cd /opt/football
.venv/bin/python sync.py backfill --seasons 2021-2026
.venv/bin/python sync.py status
```
Or press **Run backfill** on the Data tab. About 600–1,000 API calls in total.
On the free plan (100/day) it stops when the quota is used up — run it again the next day, it continues.
On Pro set `API_PER_MINUTE=280` and it finishes in one go. Note: the free plan may not include all older seasons.

After that the timer runs `sync.py update` four times a day (current season, new results, upcoming fixtures).

## Pages
- **Upcoming** — next matches with expected corners / shots / SOT / offsides / subs from both teams' home/away form
- **Teams** — averages for/against, match list, over/under checker with hit rate and fair odds
- **Leaderboard** — team ranking per competition, season and stat
- **Data** — run update/backfill, coverage per competition and season, sync history

## Files
| File | What |
|---|---|
| `config.py` | settings, competition ids |
| `db.py` | SQLite schema |
| `apifootball.py` | API client + parsing |
| `sync.py` | backfill / update CLI |
| `main.py` | web app + JSON API |
| `static/` | UI |
| `deploy/` | systemd units, install script, Cloudflare worker |

Database: `/opt/football/data/football.db`. Raw API responses are kept compressed in `raw_fixtures`,
so new stats can be extracted later without spending API calls. Odds collection (`odds_snapshots`) is the next phase.

## Public page (kasparsleitis.com/football)
The page itself is `site/football/index.html` on the website. It reads data through the replay app:
`https://telpuo.kasparsleitis.com/football-api/...` → (replay app, Docker) → `http://host.docker.internal:8095/football/api/...`.
So the football app must listen on `0.0.0.0` (see `deploy/football-web.service`) and the admin password in
`/opt/football/.env` must equal `ADMIN_KEY` of the replay app. No Cloudflare changes are needed.

Update on the server after a change here:
```bash
cd ~/kasparsleitis.com && git pull && sudo cp football-app/*.py /opt/football/ && sudo cp -r football-app/static /opt/football/ && sudo systemctl restart football-web
```

## Predictions (model.py)
Every statistic (goals, corners, shots, shots on target, offsides, substitutions) has its own model: each team
gets an attack and a defence strength, plus home advantage and competition level — a weighted Poisson
regression with shrinkage (few matches → close to average) and time decay. Counts are spread with a negative
binomial (binomial for substitutions). Everything is saved in the database:

| Table | What |
|---|---|
| `model_runs` | every model version: settings per statistic, test scores, which one is active |
| `predictions` | one per match per version: `live` (made before kick-off) or `backtest` (walk-forward test) |
| `picks` | what the tabs show (confidence / sure / easy / bet_on), scored won / lost / void after the match |
| `odds` | bookmaker prices: opening and latest |

```bash
.venv/bin/python model.py run       # predict upcoming + score finished (also runs after every data update)
.venv/bin/python model.py improve   # try other settings on the last year; keep only if better, then re-test
.venv/bin/python model.py report    # scores of every version
```
The first `run` with enough data tunes and back-tests automatically (a few minutes).
