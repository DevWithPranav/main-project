# Traffic setup (CarlaAir, Traffic Manager)

Background traffic for recordings, using CARLA's own Traffic Manager (TM) via the official
`generate_traffic.py`. Tested 2026-10-10 on Town03 (the roundabout map), Epic quality, 1920x1080.

## 1. Start CarlaAir (no built-in traffic)

```powershell
cd CarlaAir-v0.1.7-Windows11-x86_64
.\CarlaAir.ps1 Town10HD --no-traffic --quality Epic --res 1920x1080
```

The launcher always boots Town10HD, whatever map you pass. Wait for "CarlaAir is ready."

## 2. Load the map

```powershell
cd simulation\carla_scripts
python load_map.py Town03 --timeout 300
```

Run it before spawning anything. It destroys every existing actor, including the drone, and waits for AirSim to
recover. Use the `carlaAir` conda env for every Python command here. It is the only env with the `carla` package.

## 3. Spawn traffic

```powershell
cd "D:\Main-Project\project data\WindowsNoEditor\PythonAPI\examples"
python generate_traffic.py -n 70 -w 20 --asynch -s 42 --safe
```

| Flag | Meaning |
|---|---|
| `-n 70` / `-w 20` | vehicles / walkers (some spawns fail on collisions, which is normal) |
| `--asynch` | async world. **Do not use synchronous mode on CarlaAir** (see below) |
| `-s 42` | TM random seed, for repeatable runs |
| `--safe` | only vehicle blueprints suited to TM driving |

Leave the script running: **when it exits, the cars stop and are destroyed.** Ctrl+C to stop it.

## What not to do (found 2026-10-10)

- **Do not run `generate_traffic.py` without `--asynch`, or with `--hybrid`.** In sync mode it spawned the cars,
  then `world.tick()` timed out and the server hung. This happened twice (Low and Epic quality). The cause is not
  confirmed. A bare 100-tick sync test with no vehicles worked, so it is the combination.
- **Do not use `focus_traffic.py` / `traffic_flow.py` at the same time.** They also host a TM on port 8000.
- **Do not use the SUMO co-simulation on Town10HD.** The network converted from the map has 9 junctions and
  deadlocks (stopped cars climbed 9 -> 39 of 59, even in SUMO alone). SUMO is installed in the `carlaAir` env and a
  patched bridge is in `simulation/sumo/bridge/`, but the Town10HD network needs work first.

## Known limits

- Mean speed is low, about 12-15 km/h. TM drives at 70% of the speed limit by default.
- Over about 2 minutes the number of stopped cars rose from 5 to about 21 of 70. Some are at red lights, but some
  stall. Whether the roundabout gridlocks over a longer run has **not** been verified.
- Cars that stall can be removed with the blocker logic in `simulation/carla_scripts/traffic_flow.py`. That script
  also sets `ignore_vehicles_percentage`, which makes cars drive into each other, so lower it if crashes appear.

## Check it is flowing

Compare vehicle positions over a few seconds (velocity can read 0 on teleported or hybrid cars). If most cars move
less than 2 m in 3 seconds, traffic has jammed.
