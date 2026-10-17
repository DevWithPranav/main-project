# Two-machine live test: runbook and feature checklist

**Afif's PC** runs CarlaAir and streams the drone camera. **This PC** runs the detector (the live pipeline), the backend, the database
and the dashboard. The two are joined by Tailscale. Everything here was dry-run on this PC alone (a recorded flight standing in for
Afif's stream); the network part is still to be run for the first time.

```
 Afif's PC (simulator)                                 this PC (detector + dashboards)
 CarlaAir + drone + traffic                            docker: PostGIS, Redis, S3
 stream_flight.py  ── frames + pose + time ──────────► pipeline.py  (:5555, under watchdog.py)
        (TCP over Tailscale, JPEG, latest-frame-wins)   backend :8000  ◄── events / states / frames (localhost)
                                                        dashboard :5173  (browser here, optionally on Afif's PC too)
```

**This test's addresses (Tailscale, 2026-10-11):** this PC = `pranavp`, **100.72.136.117**; Afif's PC = `zenith`, **100.87.158.6**. First `tailscale ping zenith` went
via a relay (DERP Bengaluru, 75-125 ms, "direct connection not established"); this PC reports UDP ok and an easy NAT, so a direct path depends mostly on Afif's side
(see Troubleshooting: "direct connection not established").

Only port **5555** (frames) has to be reachable from Afif's PC. Backend and dashboard stay on this PC unless you add `-Dashboard`.

---

## 0. Before the day (both PCs, once)

| | This PC | Afif's PC |
|---|---|---|
| Tailscale | installed, **not connected yet** (state "NoState"): open the Tailscale tray icon, sign in / Connect | signed in to the same tailnet |
| Repo | this repo, up to date | the repo with `simulation/carla_scripts/stream_flight.py` and `services/live/transport.py` (his `afif` branch has them) |
| Python | `venv\Scripts\python.exe` | the environment he runs CarlaAir scripts in (the `stream_flight.py` header says the `carlaAir` conda env; our staging guide used `venv_sim`): needs `carla`, `opencv-python`, `pynput` |
| New files | `services/live/net_check.py`, `scripts/live_test/*.ps1` | `net_check.py` is a **single standard-library file**: copy `services/live/net_check.py` to his PC (it is not in git until you commit it) |
| Same clock | | see step 3: the test measures the offset and the pipeline corrects for it |

## 1. This PC: connect and open the port

1. Connect Tailscale. Check: `powershell -ExecutionPolicy Bypass -File scripts\live_test\01_check_network.ps1 -Peer afif`
   shows this PC's `100.x.y.z` address, Afif's address and a `tailscale ping` that says **direct** (not "via DERP", which is relayed and slow).
   If the peer name differs, run again with `-Peer <name>` (the script lists the names it sees).
2. **Once, as Administrator:** `powershell -ExecutionPolicy Bypass -File scripts\live_test\02_open_firewall.ps1`
   allows inbound TCP 5555 and 5556 **only from the tailnet range 100.64.0.0/10**. Add `-Dashboard` to also open 8000 and 5173 for Afif.
   Remove it after the test with `-Remove`.

### Optional: direct Ethernet cable instead of Wi-Fi

A plain USB-C to USB-C cable gives no network between two PCs (both ends are USB hosts). It takes an Ethernet cable between Ethernet
ports (built in, or a USB-C to Ethernet adapter), or USB4 / Thunderbolt networking on both PCs (Afif's PC has no USB4 controller, so
Ethernet it is: its Realtek GbE port). On both PCs, as Administrator:

| | Afif's PC | This PC |
|---|---|---|
| Address | `scripts\live_test\05_cable_link.ps1 -Role sim` (192.168.50.1) | `scripts\live_test\05_cable_link.ps1 -Role detector` (192.168.50.2) |
| Firewall | | `scripts\live_test\02_open_firewall.ps1 -Cable` (adds 192.168.50.0/24 to the rule) |
| Stream | `stream_flight.py --to 192.168.50.2 ...` | |

No gateway is set on the cable, so internet and Tailscale stay on Wi-Fi. After the test: `05_cable_link.ps1 -Undo` on both.
A faster link only helps if frames are lost on the way: when the pipeline log shows `received` close to what the sender sent and
`processed` far below, the detector is the limit (`-Imgsz 640`), not the network.

## 2. Check the link before touching CarlaAir

On this PC (leave it running):
```powershell
venv\Scripts\python.exe services\live\net_check.py listen
```
On Afif's PC (`<IP>` = this PC's Tailscale address from step 1):
```powershell
python net_check.py ping --to <IP>
```
Read the verdict lines:
- **bandwidth OK** (about 60 Mbit/s or more). 1920x1080 JPEG at 20 fps needs about 40. "Marginal" or "TOO LOW": add `--jpeg-quality 70` (or lower) and a smaller `--width` / `--height` to the stream command later.
- **round trip** should be a few ms to tens of ms. Above 80 ms usually means a relayed path.
- **clocks**: if it says they differ by more than 50 ms, note the `--clock-offset <seconds>` it prints (or sync both clocks: `w32tm /resync` on each PC). Without this, every latency number in the test is wrong by the clock difference.

Stop the echo server (Ctrl+C) before step 3.

## 3. This PC: start everything

```powershell
powershell -ExecutionPolicy Bypass -File scripts\live_test\03_start_stack.ps1 -Session live_test_1 -ClockOffset <seconds from step 2, or 0>
```
It starts (skipping what is already running): docker services, backend :8000, dashboard :5173, and the live pipeline under the watchdog on :5555
(loading the detector takes 20-40 s). It ends by printing the exact command for Afif. Options: `-Zones <scenario_log.json>` (staged violations, see below),
`-Dashboard` (reachable from Afif's PC), `-Imgsz 640` (faster, less accurate).
Logs: `ml\data\results\live\<session>_*.log`.

## 4. Afif's PC: simulator and stream (4 windows, as in `docs/CarlaAir_Staging_Session.md`)

| Window | Command |
|---|---|
| D (commands) | `.\CarlaAir-v0.1.7-Windows11-x86_64\StartCarlaAir.bat Town05 --no-traffic`, wait for `CarlaAir is ready.`, then `python simulation\carla_scripts\load_map.py Town05`, wait for `AirSim ready` |
| A (drone) | `python simulation\carla_scripts\goto_spot.py <x> <y> --yaw <deg>`, wait for `... 67.6 m above the road`, leave it running |
| B (traffic) | `python simulation\carla_scripts\traffic_flow.py --center <x> <y> --radius 140 --vehicles 120`, leave it running about a minute |
| C (stream) | `python simulation\carla_scripts\stream_flight.py --to <IP> --session live_test_1` (optional: `--record` also saves the flight for an offline comparison) |

Spot for the staged-violation test: Town05 around **(-40, -137)** (flight `20261009_201727` was recorded there, so its `scenario_log.json` zones fit); pass that file with `-Zones` in step 3.
For natural traffic only, any spot works. Stop with **Esc** or Ctrl+C in window C, then B, then A.

Town04, highway and zebra violations at once (`docs/Pedestrian_Violations.md`, "Town04, side by side"): in window B
run `highway_violations.py --stoppers 2 --duration 0` and, in a fifth window, `pedestrian_violations.py --station-ids 1 2 4 3
--duration 0`; fly between (-12, -5) (highway) and (256.5, -248) (crossings) by stopping window A and starting `goto_spot.py`
again with the other spot. The pipeline runs the pedestrian detector only while a crosswalk is near the view (`--people auto`,
the default; `off` / `on` with `--people`): over the highway it costs nothing (0.3-0.5 ms a frame), over the crossings about +130 ms a frame (one PC with CarlaAir:
~8-9 fps on the highway, ~4-5 fps over the crossings).

## 5. Watch it work (this PC)

Open http://localhost:5173, log in (`operator` / `operator123` runs sessions, `officer` reviews events, `planner` edits, `admin` everything).
**Live monitoring**: pick session `live_test_1`: the camera footage next to the model output (boxes, ids, speeds), the map with vehicles, events arriving.
Backend health: http://localhost:8000/docs (`GET /api/health`), live sources: `GET /api/live/sources`.

## 6. Stop and collect

```powershell
powershell -ExecutionPolicy Bypass -File scripts\live_test\04_stop_stack.ps1 -Session live_test_1
```
asks the pipeline to finish (a STOP file) so it writes its results, then prints the headline numbers. Add `-All` to stop backend and dashboard, `-Docker` the containers.
Results: `ml\data\results\live\live_test_1\` (`summary.json`: fps, per-stage ms, event latency, GPU; `events.jsonl`, `violations.json`, `kinematics.csv`, `tracks.csv`, `run_config.json`, `pipeline_gaps.jsonl` if the watchdog restarted anything).

---

## Feature-by-feature manual test checklist

Do them in this order; each needs the previous ones working. Write the result and the numbers in the tracker.

| # | Feature | How to test | Pass when | Where to read it |
|---|---|---|---|---|
| 1 | Link and clocks | step 2 | bandwidth OK, clock offset known, direct path | `net_check.py` output |
| 2 | Frames arrive | step 4 window C; look at `ml\data\results\live\<session>_pipeline.log` | log shows the hello (map `Town05`, 1920x1080) and `[live] N processed / M received`; `GET /api/live/sources` lists the session with `raw` and `annotated` | log, API |
| 3 | Live video | Live monitoring, pick the session | raw and annotated video side by side, moving, boxes with ids and speeds | dashboard |
| 4 | Detection and tracking | watch 2 minutes of dense traffic | boxes on cars / buses / trucks, ids stable (no visible id swapping), speeds plausible against the limit | dashboard; compare with `--record` flight offline |
| 5 | Vehicle states at 10 Hz | Live page map and the 3D twin in **Live** mode | markers move smoothly, colours ok / checking / flagged | dashboard, `/twin` |
| 6 | Violation events live | staged acts (`stage_violations.py` in window D, see staging guide) or natural events | each act raises an event of the right type within 3 s; clip and snapshot attached | Violations page; `summary.json` `event_latency_s_capture_to_emit` and `..._to_post_ok` |
| 7 | Review workflow | log in as `officer`, open an event, confirm / dismiss with a note | status changes, history kept, `operator` cannot review | Violations page, audit log (`admin`) |
| 8 | Statistics and exports | Statistics page, export CSV / PDF of the live session | counts per type match the list; files open | Statistics, export |
| 9 | WebSocket catch-up | in the browser, switch the network off for 20 s (or reload the page) | events of the last 60 s reappear on reconnect, no duplicates | Live monitoring |
| 10 | Database outage buffering | `docker compose stop db` for 30 s during the stream, then `docker compose start db` | red banner "Database degraded", the stream keeps running, after the restart the buffered events appear in order and `backend_events_buffered_at_exit` is 0 | dashboard banner, `backend_spool.jsonl`, `summary.json` |
| 11 | Watchdog | Task Manager: end the `python ... pipeline.py` process | the watchdog restarts it within about 10 s, Afif's stream reconnects by itself, a gap line is written | `pipeline_gaps.jsonl`, pipeline log |
| 12 | Network drop | disconnect Tailscale on Afif's PC for 15 s, reconnect | the sender reconnects, no crash, processing resumes | sender window, pipeline log |
| 13 | Offline replay | stop the backend for 1 minute during a stream, start it again, then `venv\Scripts\python.exe services\live\replay_events.py ml\data\results\live\<session>\events.jsonl` | events missed while it was down are delivered, running it twice changes nothing | Violations page |
| 14 | Configuration | Configuration page: edit a threshold, save (new version with a note) | version history shows who / when / why; a pipeline restarted with that profile uses it | Configuration, `backend\data\profiles\` |
| 15 | Zones | as `planner`: draw a no-parking zone in the 3D twin (Planner > New zone) | the zone appears, its history is kept, `backend\data\zones\Town05.json` is rewritten; the running pipeline logs `[zones] reloaded N zone(s)` within ~2 s (no restart, no `-Zones` needed) and flags a car stopping in it | twin, file |
| 16 | 3D twin live | `/twin`, Live mode, with the **Custom traffic** switch off and on | live vehicles and pins; custom traffic (blue) is independent of the video traffic | twin |
| 17 | Scenario builder (no CARLA needed) | right-click the Town05 map, Add Custom Rules, add a rule + a traffic scenario, Simulate virtual traffic, Report | virtual traffic follows the plan; comparison with intervals; PDF downloads | twin |
| 18 | Roles and audit | log in as each role | each sees only what its role allows; `admin` sees the audit log with the review and zone actions | all pages |
| 19 | Performance numbers | after the run, `summary.json` | write down: fps processed, drop count, detect+track ms p50, event latency p50 / max (target <= 3 s), GPU utilisation and memory | `summary.json` |

Also record from the same run: frames received / processed, `backend_posts_failed`, number of events by type against the staged list (precision / recall by eye), and the
`net_check` numbers.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `01_check_network.ps1`: "no Tailscale address" | Tailscale is not connected on this PC: tray icon > Connect / sign in. |
| `tailscale ping` says "direct connection not established" | the PCs cannot punch through to each other. On Afif's PC run `tailscale netcheck` (UDP must be `true`; "MappingVariesByDestIP: true" means a hard NAT), allow `tailscaled` / UDP 41641 in his Windows firewall, make sure no VPN or exit node is active, then run `tailscale ping -c 15 pranavp` from his side: the path often upgrades to direct after some traffic. Two PCs on the same Wi-Fi go direct over the LAN. If it stays relayed, stream at 1280x720, `--jpeg-quality 70` (about 10 Mbit/s); note this changes the input the detector sees, so compare with 1080p numbers carefully. |
| `tailscale ping` says "via DERP" | relayed path: slow; both PCs need direct connectivity (try again after a minute; same Wi-Fi helps). |
| `net_check.py ping` cannot connect | `listen` not running, wrong IP, or the firewall rule is missing (step 1.2). |
| `stream_flight.py` connects and drops | pipeline not listening (`netstat -ano | findstr 5555`), or a firewall prompt was dismissed on this PC. |
| pipeline log: `Failed to load` / CUDA errors | another process holds the GPU; close other GPU programs; try `-Imgsz 640`. |
| latency numbers are huge or negative | the two clocks differ: repeat step 2 and restart with `-ClockOffset`. |
| few frames processed | latest-frame-wins by design (the detector is slower than the camera); `summary.json` counts the drops. Use `-Imgsz 640` or a lower `--width` on the sender. |
| no events at all | the map has no flagged behaviour in view: stage violations (see the staging guide) or watch longer; check the pipeline log for `[event]` lines. |
| Live page shows nothing | pick the session by name; `GET /api/live/sources` must list it (frames in the last 10 s). |
| "Not Found" in the builder | an older backend is running: restart it (`04_stop_stack.ps1 -All`, then `03_start_stack.ps1`). |
