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

## Betsafe prices
API-Football has no Betsson/Betsafe, so Bet365 is collected (it has corners, shots and offsides). About once a week
`betsson.py` fetches Betsson's prices (= Betsafe, same platform) for result and goals from The Odds API, using the
tennis app's `ODDS_API_KEY` (2 credits per competition, ~16 a week; stops when fewer than `ODDS_API_RESERVE`=150 are
left). It measures Betsson ÷ Bet365 per market and price range (`price_ratio`), and Bet on uses Betsson's real price
where it has one, else Bet365's price × that ratio ("Betsafe est."). The record is scored at that Betsafe price.

## All match details, stadiums and weather
`extract.py` unpacks the stored API responses (no API calls): goals and cards with minutes, VAR, lineups and
formations, coaches, and every player's match statistics. `weather.py` adds stadiums (city, pitch surface, capacity)
and the weather in the 2 hours from kick-off from Open-Meteo (free): measured for played matches, forecast for
upcoming ones. Both run with every data update. `factors.py` checks whether weather, pitch, rest days, coach changes
or referees explain anything the model's own predictions miss (workflow actions `collect` and `factors`).

## Other games of our teams
`other.py` fetches every other game of the teams in our competitions (domestic leagues of teams outside the top 5,
domestic cups, super cups) with their statistics, into `other_matches` / `other_team_stats`. The tabs stay on our 8
competitions; rest days count every game; and the model may learn from them: `improve` tries it per statistic and
keeps it only where predictions of our competitions get better. Opponents outside our competitions count as their
league's average team. First run: workflow action `other` (~2,800 API calls), then once a day automatically.

## Patterns in the model (features.py)
Kick-off time, short rest (3 days or less, any competition), new coach (first 6 games), weather (rain, heavy rain,
strong wind, cold, hot), artificial pitch, and the referee are fitted together with the team strengths. `improve`
adds a group to a statistic only if walk-forward predictions get better overall AND in both halves of the test
year, so chance wins are not kept. Cards (yellow + red) are a statistic like the others; the referee mostly matters
there. Every Monday night the model is re-tuned with everything collected and a factor analysis is saved.

## Players, injuries, lineups, table, xG, travel
`players.py`: injured / suspended players for every competition and season (`/injuries`), and confirmed lineups
~1 h before kick-off (`lineups.py`, every 10 minutes from the user's crontab — added by `autoupdate.sh`); the match
is re-predicted when both XIs are in. Every API response is kept compressed in `raw_api`.
Model groups: `lineup` (today's XI vs the usual XI, player value = recent minutes-weighted rating), `absent` (share
of the usual team missing), `stakes` (league table late in the season), `xg` (goals minus xG, last 8 games),
`travel` (away trip). Pinnacle's prices are kept as the market's view for Bet on. Same rule as all patterns:
used only where walk-forward predictions get better in both halves of the test year.

## Every night: pattern search and re-tune
`discover.py` tests ~20 ideas from the stored data (playing style, fouls, form, fixture congestion, a league game
after a European midweek, promoted teams, back three, referee's card / foul history, derbies, early season,
Christmas period, late European kick-offs) against the active model's walk-forward errors: found on the older
years (|z| >= 3), confirmed on the last year (same direction, |z| >= 2). Confirmed ideas become factor groups
(`auto_<name>`) that the re-tune may use. The re-tune runs every night (01-07 UTC); a change to which patterns a
statistic uses goes live only when two runs in a row agree. Results: Data tab, workflow actions `discoveries` / `discover`.
