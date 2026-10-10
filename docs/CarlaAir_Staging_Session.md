# CarlaAir staging session (Build Plan M2): step by step

Records staged violations for every vehicle condition CARLA can stage, so each one can be scored
on real perception. Prepared 2026-10-10. Every spot below was dry-run offline (planned paths through
the rules), and all 91 acts behave as planned.

**Covers 22 conditions:** A1 A2 A4 A5 A6 A8 · B1 B3 B4 B5 · C1 C2 C3 C4 C5 · D1 D2 D3 D4 · E1 E4 · F1.
Not here: A3 (no CARLA town has a forbidden lane change across a non-solid line; it is covered by
unit tests and real sites) and F2 / F3 / F5 (need pedestrian detection, M3).

**Time:** about 30 min of staging, plus setup. 5 spots in 2 towns, so 5 recordings.

## The spots

| # | Town | X | Y | Heading | Acts | New conditions |
|---|---|---|---|---|---|---|
| 1 | Town03 | -30.1 | 111.2 | 0 | 21 | A1 A4 A6 A8 B3 B4 C1 C2 C4 E1 E4 F1 |
| 2 | Town03 | 169.9 | 161.2 | 0 | 19 | A5 B1 C3 D1 D4 |
| 3 | Town03 | -130.1 | -38.8 | 0 | 15 | A2 D2 D3 |
| 4 | Town03 | 119.9 | 61.2 | 90 | 13 | C5 |
| 5 | Town05 | -253.6 | 11.6 | 0 | 23 | B5 |

You don't fly by hand. `goto_spot.py` puts the drone at the spot, 67.6 m above the road, and holds it
there. The stager reads the drone's real position and plans inside the actual camera view.

---

## Step 0: once, before you start

Open **4 PowerShell windows**, all in the repo folder, and run this in each:

```powershell
cd C:\Users\prana\Desktop\Main-Project\main-project
venv_sim\Scripts\activate
```

They are named in the steps below:

| Window | Runs | Stays open? |
|---|---|---|
| **A: drone** | `goto_spot.py` (holds the drone) | yes, during a spot |
| **B: traffic** | `traffic_flow.py` (background cars) | yes, during a spot |
| **C: recorder** | `record_flight.py` | yes, until you stop it |
| **D: commands** | everything else | n/a |

## Step 1: start CarlaAir (window D)

```powershell
.\CarlaAir-v0.1.7-Windows11-x86_64\StartCarlaAir.bat Town03 --no-traffic
```

Wait for `CarlaAir is ready.`. It always opens Town10HD whatever map you ask for, which is why
Step 2 is needed. Skip this step if CarlaAir is already running.

## Step 2: load the town (window D)

```powershell
python simulation\carla_scripts\load_map.py Town03
```

Wait for `[load_map] AirSim ready`. This can take about 25 s; don't restart anything while it waits.

---

## Step 3: record one spot (repeat for spots 1 to 4)

The commands below are for **spot 1**. For other spots, swap in that spot's X, Y and heading from the table.

**3a. Window A: put the drone at the spot**
```powershell
python simulation\carla_scripts\goto_spot.py -30.1 111.2 --yaw 0
```
Wait until it prints `... 67.6 m above the road ... (0.0 m from the spot)`. Leave it running.

**3b. Window B: start traffic around the spot**
```powershell
python simulation\carla_scripts\traffic_flow.py --center -30.1 111.2 --radius 140 --vehicles 120
```
Leave it running. Wait about 1 minute until its `[flow] ... in radius` count is near 120.

**3c. Window C: start recording**
```powershell
python simulation\carla_scripts\record_flight.py --vehicles 0 --labels --no-control
```
Wait until it shows `Recording... N frames saved`.

**3d. Window D: stage the violations** (into the new flight's folder)
```powershell
$flight = (Get-ChildItem simulation\data_export\recorded_flights -Directory | Sort-Object Name | Select-Object -Last 1).FullName
$flight
python simulation\violation_scenarios\stage_violations.py --out "$flight\scenario_log.json"
```
Check that `$flight` printed today's newest folder. Then wait. The stager runs its acts one after
another and finishes with `[done] N violations, M negatives -> ...`.

**3e. Stop recording**
Click window C and press **Esc** (or Ctrl+C). Wait until it prints `[done] ... frames` and the
`[labels] check video` line. Saving and auto-labelling take a few minutes; don't close it before then.

**3f. Stop traffic and the drone**
Press Ctrl+C in window B, then in window A.

**3g. Write down the flight folder name** (the `$flight` value), for example `20261010_153012`, with its spot number.

Then go back to 3a with the next spot.

| Spot | 3a: goto_spot | 3b: traffic_flow --center |
|---|---|---|
| 1 | `-30.1 111.2 --yaw 0` | `-30.1 111.2` |
| 2 | `169.9 161.2 --yaw 0` | `169.9 161.2` |
| 3 | `-130.1 -38.8 --yaw 0` | `-130.1 -38.8` |
| 4 | `119.9 61.2 --yaw 90` | `119.9 61.2` |
| 5 | `-253.6 11.6 --yaw 0` | `-253.6 11.6` |

## Step 4: spot 5 (Town05)

Window D: `python simulation\carla_scripts\load_map.py Town05`, then wait for `AirSim ready`.
Then do Step 3 with spot 5's numbers.

## Step 5: hand over

Send Claude the 5 flight folder names with their spot numbers. Claude processes and scores them
(the commands are below). If anything went wrong at a spot, say which one; it can be re-recorded alone.

---

## If something goes wrong

| Problem | Fix |
|---|---|
| `Drone actor not found` | CarlaAir isn't fully up, or `load_map` is still resyncing. Wait 30 s and retry. |
| AirSim timeouts right after `load_map` | Normal for up to about 25 s. Wait, don't restart. |
| Drone not at 67.6 m or drifting | Ctrl+C window A and run 3a again. |
| Stager prints fewer acts than the table | Check the drone position line in window A (it should be 0.0 m from the spot), then re-do the spot. |
| CarlaAir freezes or crashes | `.\CarlaAir-v0.1.7-Windows11-x86_64\StopCarlaAir.bat`, then start again from Step 1. Re-record the spot you were on. |

Optional check before staging (offline, no simulator), which shows the acts planned for a spot:
`python simulation\violation_scenarios\stage_violations.py --plan-only --town Town03 --center -30.1 111.2 --yaw 0 --out plan_check\scenario_log.json`

## After the session (main venv; Claude runs these)

```powershell
python ml/violation_engine/process_recorded_flight.py <flight> --tracker tracktrack_ours
python ml/violation_engine/run_violations.py <flight> --profile town05 --scene ml/violation_engine/configs/scenes/<Town>.json --zones <flight>/scenario_log.json
python ml/violation_engine/run_violations.py <flight> --oracle --profile town05 --scene ml/violation_engine/configs/scenes/<Town>.json --zones <flight>/scenario_log.json
python ml/violation_engine/eval_violations.py <pipeline violations dir> <oracle violations dir> --scenario <flight>/scenario_log.json
```

The scorer reports per type, **per condition** (detected / detected as another condition / missed),
and false alarms on the negative acts. The scenario log carries the rule settings its acts need
(bus lane, no-U-turn junction, truck limit), so `--zones <log>` applies them.

## Finding other spots

`python simulation/violation_scenarios/stage_violations.py --suggest --town Town04` lists the fewest
drone spots that cover every condition in a town (offline, 5 to 25 min per town).
