# CARLA Traffic Manager: which violations can be simulated reliably

Date: 2026-10-14. Scope: pick a small set of violations that CarlaAir can produce on demand, and write down the Traffic Manager (TM) problems at high density. **The density problems are not fixed here.**

## How this was checked

| Source | What it gives | Confidence |
|---|---|---|
| `dir()` on the installed `carla` package (carlaAir env) | The TM, walker and traffic-light calls that exist in this build | Verified: the call exists |
| Repo measurements (`docs/Highway_Violations.md`, `docs/Traffic_Setup.md`, tracker, 2026-10-10 / 11) | How TM and the scripted cars behaved in CarlaAir | Measured on this setup |
| General CARLA documentation and experience | What a call is meant to do | **Not tested in CarlaAir.** Marked "docs only" below |

Nothing new was run in the simulator for this document. A call that exists is not a call that behaves, and CarlaAir already broke several (see "Known TM problems").

## What CARLA gives us

**TM calls present in this build:** `ignore_lights_percentage`, `ignore_signs_percentage`, `ignore_vehicles_percentage`, `ignore_walkers_percentage`, `vehicle_percentage_speed_difference`, `global_percentage_speed_difference`, `set_desired_speed`, `force_lane_change`, `auto_lane_change`, `random_left/right_lanechange_percentage`, `distance_to_leading_vehicle`, `collision_detection`, `vehicle_lane_offset`, `global_lane_offset`, `keep_slow_lane_rule_percentage`, `set_path`, `set_route`, `update_vehicle_lights`, `set_synchronous_mode`, `set_hybrid_physics_mode`, `set_random_device_seed`.

**Other calls:** `TrafficLight.set_state / freeze / set_red_time`, `world.freeze_all_traffic_lights`, `world.set_pedestrians_cross_factor`, `WalkerAIController.go_to_location / set_max_speed`, `Actor.set_transform` and `set_simulate_physics` (put a car on a path with physics off), `Map` waypoints, lane markings, crosswalks, speed-sign landmarks, `Vehicle.get_speed_limit`.

**There is no** "violate rule X" switch. TM cars follow the rules by default. A violation is either a TM setting that loosens one rule, or a car we drive ourselves.

## Two ways to make a violation

| Way | What it is | Control | Used so far |
|---|---|---|---|
| **TM-driven** | Give a car to the TM and loosen one rule (`ignore_lights_percentage`, speed difference, ...) | Low. The TM decides the path, timing and lane changes. | Background traffic only |
| **Scripted (kinematic)** | We set the car's pose every tick along a planned path, physics off | Exact. Speed, timing, position are as planned | `stage_violations.py`, `pedestrian_acts.py` |
| **Scripted (dynamic)** | Our own pure-pursuit driver on a physics car | Medium. Good, not exact (about 10% of time over 1 m off lane centre at 108 km/h) | `highway_violations.py` |

Kinematic acts are the only ones that were reproducible every time in this repo. TM-driven violations were never measured.

## The violations, one by one

Difficulty: Low (a few lines, already built) / Medium / High. Condition ids are the engine's (A1 ... F5).

| Violation | Condition | How in CARLA | Existing APIs | Difficulty | Reproducible and controllable | Dependencies and limits | Native or custom |
|---|---|---|---|---|---|---|---|
| **Speeding** | E1, E3, E4 | Kinematic car at 1.3 x or 1.6 x the lane limit, plus a negative at 1.1 x (inside tolerance) | `Map` speed-sign landmarks for the limit, `set_transform` | Low | **Yes.** Built and dry-run (`speeding` act; E4 with a truck over its class limit) | Needs a speed sign on the road. Do **not** use the TM above about 60 km/h: it steers to full lock (measured, 2026-10-11). TM cars see a 30 km/h limit only | Custom |
| **Wrong-way driving** | C1, C2, C4, C5 | Kinematic car drives a lane against its direction at 30 km/h | Lane waypoints, `set_transform` | Low | **Yes.** Built (`wrong_way` act; C2 from a junction, C4 on a ramp, C5 on a one-way) | C5 needs a one-way road (Town05 has none in view). C4 needs a ramp (Town04 / Town05) | Custom. TM cannot do it (`set_path` is meant for legal routes, docs only) |
| **Stopping where forbidden** | B1, B3, B4, B5, F1 | Kinematic car stops 30-45 s in the zone, plus a short-stop negative | Zone from the scenario log, `set_transform` | Low | **Yes.** Built | Zone must exist in the engine scene. Highway B1 / B2 in live traffic is **not** reliable: TM cars hit the stopped car (`after_crash`), yield is low | Custom |
| **Illegal lane change / crossing a solid line** | A2, A3, A4, A8 | Kinematic lane change across a solid line, a cut-in (TTC 0.9 s) ahead of a 50 km/h car | Lane marking type from waypoints | Medium | **Yes** for A2, A4, A8 (built). A3 cannot be staged in any CARLA town (no forbidden change across a non-solid line) | Needs the right marking at the drone's view | Custom. `force_lane_change` is the only TM route and it does not pick the line, docs only |
| **Improper overtaking** | A2 plus C1 | Kinematic overtake across a solid centre line. The engine raises wrong-way too (declared `also` in the log) | Same as above | Medium | **Yes**, one act, but it counts as two conditions | Straight solid centre line in view (Town05 has none) | Custom |
| **Red-light running** | red light | Switch the light with `TrafficLight.set_state`, freeze the others, drive a kinematic car through the stop line at 30 km/h with red 1.5 s / 3 s before, plus a negative (red 1.5 s after) | `set_state`, `freeze`, `world.freeze_all_traffic_lights` | Low to build, **but out of scope since 2026-10-09** | Yes in the stager (`--red-light`), off by default | Needs a signalised junction. The engine's red-light rule is optional | TM has `ignore_lights_percentage` (exists, **not tried here**); the stager does it by hand |
| **Stop-sign violation** | none | `ignore_signs_percentage` exists in the TM. Stop-sign handling in this build is **unknown** | `ignore_signs_percentage`, sign landmarks | Unknown | **Not verified** | The engine has no stop-sign condition, so nothing would score it | Not recommended |
| **Pedestrian crossing violations** | F1, F2, F3, F5 | A CARLA walker is moved like the cars (physics off) on the crosswalk. F3: car drives on when the walker is 2 m short. F2: car stops 6 s while a walker waits. F5: car stops 9.5 s with a walker 3.5 m away | Walker blueprint, `set_transform`, crosswalks from `get_crosswalks()` | Medium | **Yes.** Built (`pedestrian_acts.py`) and dry-run. F3, F2, F5 also depend on the walker's pose, which is logged | Walkers use physics off. The walker AI (`WalkerAIController`) and `set_pedestrians_cross_factor` give random crossing, not a timed act. `ignore_walkers_percentage` makes TM cars skip walkers, not reproducible | Custom |

Not in the table: A1 (wrong lane through a junction), A5 (restricted lane), A6 (shoulder), D1-D4 (U-turns). These are built as kinematic acts too (`plan_road_feature_acts`), each with a negative, but each needs a specific road feature in view, so they depend on the drone spot.

## Recommendation: initial scope

Start with **five** violations. All five are scripted and kinematic, already built and dry-run offline, and none needs the TM to cooperate.

| # | Violation | Condition | Why this one |
|---|---|---|---|
| 1 | Speeding (1.3 x, 1.6 x, negative 1.1 x) | E1 | Works on any road with a speed sign. Exact speed |
| 2 | Wrong-way driving | C1 | Works on any two-way road |
| 3 | Stopping in a no-stopping zone | B3 | Needs only a zone, no road feature |
| 4 | Stopping on a zebra crossing | F1 | Needs only a crosswalk. Also the base for F2 / F3 / F5 later |
| 5 | Illegal U-turn in a prohibited zone | D1 | Needs only a zone. Built with a negative outside the zone |

Add next, in this order, only after the five are recorded and scored: **F3 / F2** (pedestrian), then **A2 / A8** (lane), then **red light** (the stager already has it behind a flag).

Leave out for now: stop-sign violations (no engine condition, TM behaviour unknown), improper overtaking (two conditions in one act, hard to score), TM-driven violations of any kind, highway B1 / B2 in live traffic.

**Native vs custom.** Nothing recommended is a native CARLA violation. What CARLA gives natively is the map (waypoints, markings, crosswalks, sign landmarks), traffic-light control, walker spawning and the pose calls. The violation itself is custom scripting in every case.

## Known TM problems (not fixed here)

### Measured in this repo

| Problem | Evidence |
|---|---|
| TM steering is unstable above about 60 km/h | `set_desired_speed` above 60: full lock, every planted speeder hit the guardrail |
| TM cars see a 30 km/h limit | `get_speed_limit()` returns 30, median background speed 20 km/h |
| TM default speed is low | Mean 12-15 km/h in Town03 (70% of the limit) |
| `vehicle_lane_offset` does nothing | 3.0 m offset gave 0.00 m |
| `keep_slow_lane_rule_percentage` raises `rpc_error` | Skipped |
| `set_target_velocity` at spawn breaks TM steering | Car left steering at full lock |
| Cars stall | Stopped cars rose from 5 to about 21 of 70 in 2 minutes (Town03 roundabout) |
| TM traffic crashes on its own | 641 contacts in 5 min with 64 cars, about 290 with 30 cars (Town04 road 45); pile-ups at both merges |
| `ignore_vehicles_percentage` makes cars drive into each other | Used in `traffic_flow.py` to clear stalls |
| Synchronous mode hangs the server | `generate_traffic.py` without `--asynch` or with `--hybrid`: `world.tick()` timed out, twice |
| Epic quality is slow | About 16 fps with traffic; Medium about 47 fps |
| Two TMs on one port | `traffic_flow.py` / `focus_traffic.py` and `generate_traffic.py` cannot run together |
| SUMO co-simulation deadlocks on Town10HD | Stopped cars 9 to 39 of 59, even in SUMO alone |
| Background TM cars create false "violations" | About 50 B2 and 15 B1 per 5-minute run; they are TM artifacts and are listed separately |

### Not measured, so unknown

- **Pedestrian density:** the repo only ran 20 walkers (`generate_traffic.py -w 20`). Nothing was measured for crowded crosswalks, walkers blocking junctions, or walker AI behaviour near stopped cars.
- **Density thresholds:** the only points are 30 cars (about 290 contacts) and 64 cars (641) on one road. There is no curve, and the numbers are for Town04 only.
- **Roundabout gridlock over a long run:** "has not been verified" (`Traffic_Setup.md`).
- **Whether `ignore_lights_percentage`, `ignore_signs_percentage`, `force_lane_change` and `ignore_walkers_percentage` work in CarlaAir:** they exist, none was tested here.

### For later

Possible directions, none tried: a custom background driver like the one the planted cars use instead of the TM; a cap of about 30 cars per road; hybrid physics tuning; fixing the speed-limit sign link so TM cars see 90 km/h.
