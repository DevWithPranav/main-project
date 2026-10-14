# Pedestrian-crossing violations on a loop (F3, F1), Town05

Script: `simulation/violation_scenarios/pedestrian_violations.py` (carlaAir env). Tests:
`ml/violation_engine/tests/test_pedestrian_violations.py`.

It repeats the zebra acts of `stage_violations.py` / `pedestrian_acts.py` so that an F3 or F1 event happens every
few seconds, not once per flight. A *station* is one crosswalk and the lane through it. Each station plays its acts
one after the other, in a shuffled order each round, and the stations run side by side.

| Act | Condition | What happens | Counts as |
|---|---|---|---|
| Drives through | F3 failure to yield | The car passes at 30 km/h while a walker crosses (the car meets the walker 2 m short of its side) | violation |
| Yields | F3 | The car stops 2 m short and lets the walker cross | negative |
| Stands on it | F1 stopped on the crossing | The car stops 15 s on the crossing, nobody about | violation |
| Short stop | F1 | The same for 6 s, under the 10 s rule | negative |
| Blocking, obstructing | F2, F5 | Only with `--conditions F3 F1 F2 F5` | violation and negative |

## Design

- **Nothing is driven by the Traffic Manager.** Cars and walkers are moved kinematically (physics off, pose set every
  tick along a planned path), as the stager does. F3 and F1 are judged to seconds and centimetres, and the TM would
  drive into a car stopped on the crossing and choose its own timing. Cars here move at 30 km/h on a fixed path, so
  there is no reason for a physics driver as on the highway.
- **The plan is the truth.** The scenario log has every act's actor id and carla frames, in the format
  `eval_violations.py --scenario` reads. The judge is the engine: record a flight over the stations
  (`record_flight.py --labels`), then `run_violations.py --oracle`.
- **No spawning while it runs.** Each station spawns one car and one walker, once. Between acts they are parked
  3 km away and 100 m down. Every spawn froze CarlaAir about 0.6 s on the highway, and the oracle only follows
  vehicles inside the camera's view.
- **6 s between acts at a station**, longer than the stop rules' 2 s close gap, so two stops never merge into one
  episode (`StopInZoneMonitor` keeps one timer per spot).
- **Background traffic:** none for the clean run. Start TM traffic (30 km/h is stable) only well away from the
  stations, later, to test the detector with clutter. The script warns if other vehicles are within 80 m of a station.

## Run it

Offline, no simulator:

```powershell
cd simulation\violation_scenarios
python pedestrian_violations.py --town Town05 --list-stations
python pedestrian_violations.py --town Town05 --plan-only --station-ids 0 1 --conditions F3 F1 F2 F5 --rounds 3 --out plan\scenario_log.json
cd ..\..
venv\Scripts\python.exe ml\violation_engine\run_violations.py --plan simulation\violation_scenarios\plan\scenario_log.json --scene ml\violation_engine\configs\scenes\Town05.json --profile town05
```

Live (CarlaAir up on Town05, drone over the stations, `record_flight.py --labels` recording):

```powershell
python pedestrian_violations.py --station-ids 0 1 --duration 600 --out <flight folder>\scenario_log.json
```

Options: `--stations N` (best-scored N, default 3), `--all-stations`, `--conditions`, `--gap-s 6`, `--rounds`, `--seed`.
Stations 0 and 1 are at (-270.5, -11.5) and (-270.7, 14.7), 26 m apart, so one drone spot covers both: centre about
(-270.6, 1.6). The station numbers are the crosswalk's index in `Map.get_crosswalks()`, so they are stable for a map.

## What was checked offline (2026-10-14, no simulator)

| Check | Result |
|---|---|
| Existing zebra acts at a Town05 crosswalk, through the engine's rules | 7/7 as expected (F1, F3, F2, F5 and their negatives) |
| `stage_violations.py` after moving `crosswalk_polygons` and `plan_entry` out of it | Plan log identical to before |
| All 66 Town05 crosswalks, 4 acts each (F3, F1, with negatives) | **260/264 acts as expected; 65 of 66 stations fully clean.** Station 42 raises an extra `lane_violation` on all four acts |
| Stations 0 and 1, all four conditions, 3 shuffled rounds | 42/42 |
| Unit tests (Runner clock and log, parking, schedule, station search on Town05) | 10 pass in the carlaAir env; skipped in the main venv. Engine suite 212 OK |

These are planned paths with perfect positions fed to the rules. They show the rules and the staging agree. They
say nothing about the live run or about detection from the drone's video.

## What was checked live (2026-10-14, CarlaAir on Town05, no traffic)

| Check | Result |
|---|---|
| Two 90-110 s runs, stations 0 and 1 | Actors persisted the whole run (no respawn). Parking worked: z -100 while parked, road level (cars 0.02, walkers 0.95) while active. 6-8 acts each, no errors |
| True positions of the staged acts (an independent watcher polling every tick) | F1 stands 15.0 s on the crossing, the short stop 6.0 s, F5 9.5 s; the F3 car crosses at 30 km/h in 0.3 s with the walker 3.0 m away (yield negative: 5 m), as planned. CARLA reports zero velocity for actors moved with `set_transform`, so speeds here come from position differences |
| Flight `20261014_224953` (drone at (-270.6, 1.6), 67.6 m, heading 0; 200 s, 16 acts: 4 F1 + 4 F3 violations, 4 + 4 negatives) | Recorded 7,386 frames; the truth logs hold exactly 2 vehicles and 2 walkers |
| **Engine on CARLA's true positions** (`run_violations.py --oracle`, scored with `eval_violations.py --scenario`) | **8/8 violations detected, each as the staged condition (F1 4/4, F3 4/4); 0 false positives; 0 of 8 negatives triggered.** Median flag delay 7.0 s |
| Detector + tracker on the video (`process_recorded_flight.py --people`, TrackTrack) | Ran on all 7,392 frames: 18 vehicle track IDs, 7 pedestrian track IDs (14,469 rows). **Not scored yet** |

Throughput: about 4.8 acts per minute from two stations (the F1 acts are 24 s and 12 s long).

A geometric screen (junction-free 30 m approach) would have kept only 2 of 66 crosswalks, because most zebras in Town05
sit at junction mouths. The engine does not mind, so the stations are ranked best-first (unsignalised, few junction
lanes on the way in and out) and the engine's dry-run is the real filter.

## Not done

- **Scoring the real pipeline** (`run_violations.py` without `--oracle`, then `eval_violations.py` pipeline vs oracle
  vs `--scenario`) on flight `20261014_224953`. The detector and tracker have run; stitching and the score have not.
- **Pedestrian detection from the drone** is the weak link: the pedestrian row in the detection table is 0.540 / 0.258.
- **Signals:** CARLA does not log pedestrian lights, so a walker crossing against a red man still counts. Stations
  with a traffic light within 60 m on their road are ranked last, and the engine tags such events
  `signalised_crossing` for review.
- Stations 56 (-259.9, 1.4) and others near 0 and 1 share the junction area; run them together only after
  checking that the cars do not cross each other's paths.
