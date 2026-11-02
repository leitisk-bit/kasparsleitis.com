# Indoor O Replay

Map library and route replay for indoor orienteering (telpu orientēšanās).

Two kinds of users:

- **Admin** (signs in with the admin password): uploads course maps (PDF), checks how the map was read, and imports the race results with split times (lof.lv **Split** page saved as PDF or web page).
- **Everyone else**: opens a map, adds their **TCX** watch file and chooses whose run it is. The server checks that the file fits: it must be from the race day and the watch must have run for the whole race (at most 30 s shorter or 10 min longer than the result). Runners whose result time is closest are offered first. If that person already has a run on the map, the new one replaces it. All saved runs are open for everyone to replay, compare, and view in 3D.

How it works:

- **Maps:** the server reads floors, stairwell letters, stair flights (red = up, blue = down), controls in course order, one-way arrows, four-way crossings and tape, then builds a route network that never crosses walls or tape. It also lines the floors up by their stairwells for the 3D view.
- **Runs:** the route of each leg is worked out from the split times and the watch altitude (floor changes show clearly). If the watch was started before the start, the start is found in the recording automatically.
- **Replay:** one run with its fastest-option ghost, or several runs compared; switch between the map and 3D.

## Install on Ubuntu

```bash
unzip oreplay.zip && cd oreplay
sudo ADMIN_KEY='choose-an-admin-password' bash install.sh
sudo cp nginx-oreplay.conf /etc/nginx/sites-available/oreplay   # edit server_name first
sudo ln -s ../sites-available/oreplay /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

The service runs on `127.0.0.1:8090` (`systemctl status oreplay`, logs: `journalctl -u oreplay -f`).
Data lives in `/opt/oreplay/data` (one folder per map, one JSON file per run). Back up that folder.

The admin password is entered once per browser with **Admin sign in**. To update the app later, unzip the new version and run `sudo bash install.sh` again; maps, runs and the password are kept.

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
