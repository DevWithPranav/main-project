# Highway violations in scripted highway traffic (E1, B1, B2), Town04

Planted cars break highway rules in scripted traffic on Town04, and every car in the world is judged with
CARLA's true positions. Script: `simulation/violation_scenarios/highway_violations.py`.
Use the `carlaAir` conda env for every command.

Since 2026-10-14 the script is set up for high-speed (E1 speeding) studies. Every car on the stretch is driven
by the script (`HandDriver`), none by the Traffic Manager (TM). The TM cannot do it (see the CarlaAir problems
below), so the background traffic is scripted too.

| Cars | What they do |
|---|---|
| Slow cars (background) | `--slow-cars 12`, each at a fixed speed from `--slow-kmh 50 80`, below the limit. They keep right and pass only a car that holds them below 0.7 x their speed. 4 in 5 are placed (and recycled) into the two outer lanes, so the inner lanes stay free for passing. |
| E1 speeders | `--speeders 8`, given the `--speed-tiers 1.03 1.2 1.4 1.6` in turn: 93 / 108 / 126 / 144 km/h in a 90 zone. 1.03 is inside the 5 km/h tolerance, so those cars are negatives and should produce no E1. (1.05, 94.5 km/h, overshot to 95-96 and was flagged.) |
| B1 stoppers | Off by default (`--stoppers 0`). Cruise at 63 km/h, then every 60 s brake to a stop in the lane, hold 40 s with hazards on, drive on. |
| B2 shoulder stoppers | Off by default (`--shoulder-stoppers 0`). The same, from the outer lane: onto the 3.5 m shoulder at 20 km/h first, back into the lane afterwards. |

### The stretch

The default is the northbound straight on Town04's east side: lanes 3-6, x about -6 to -17, y -218 to 207.
It is 425 m posted at 90 km/h, with 4 lanes, a 3.5 m outer shoulder and bends of about 200 m radius at both
ends. Defaults: `--center -10 -10 --radius 210 --heading 90`. The southbound side is left out: the scene drops
it to 60 and then 30 km/h near y -40.

The old default, road 45, is a 180 m radius arc. At the 3 m/s2 bend cap any car is held to about 84 km/h there,
so there is no room for high speeds. It still works with `--center -311.6 436 --radius 230 --heading any`.

Why Town04 and not Town06: Town06 has no speed-limit signs and its shoulders are 0.5 m wide, so the engine would
never treat it as highway and B2 could not happen. The engine's scene for Town04 is `configs/scenes/Town04.json`.

### The driver

- **Lane following:** pure pursuit plus a cross-track correction. At a fork it takes the mainline branch
  (`mainline()`: not a ramp or non-highway lane, then the same lane id, then the least turn). `track()` keeps the
  car on the mainline when `get_waypoint()` returns a ramp lane near an exit gore.
- **Speed:** the desired speed (fixed, or tier x the posted limit), held down by:
  - `follow_speed`: an IDM-like approach to a slower lead. Brake at 3 m/s2 so as to reach a 2.5 s + 15 m gap
    at the lead's speed.
  - Time to collision with the lead below 2 s: full brake.
  - `curve_cap`: brake at 3 m/s2 for any bend up to 180 m ahead, to a 3 m/s2 lateral limit.
- **Passing:** when the lead holds the car below its pass threshold, it moves into a neighbour lane that is
  faster and clear, left first. "Clear" includes a closing-speed check: no car ahead or behind in that lane may
  be less than 4 s away at the closing speed. The car returns right when that lane is clear, with 1.5 s of
  room in front of the car it passed.
- **Lane changes** blend sideways over 3.5 s and turn back if the target lane stops being clear in the first
  half. While a change is going on, its target lane is visible to every other driver (`goal_of`), so they
  treat the car as already on it.

### Cars are recycled

Every car is spawned parked at start-up, spread along the stretch (violators first, then the slow cars around
them). The collision sensors are attached, and then every car is launched at speed together. A car that leaves
the radius, stays below 5 km/h for 10 s, or is off the stretch's lanes for 2 s (a ramp, a crossing road) is
teleported (`set_transform` + `set_target_velocity`) to the clearest upstream spot, at speed. At most one car
is recycled per 0.1 s sample. Each pass is a new E1 event. Only a car that was destroyed is spawned again.

## Run it

```powershell
cd CarlaAir-v0.1.7-Windows11-x86_64
.\CarlaAir.ps1 Town10HD --no-traffic --quality Medium --res 1280x720

cd ..\simulation\carla_scripts
python load_map.py Town04 --timeout 300

cd ..\violation_scenarios
python highway_violations.py --duration 300
```

Do not run `traffic_flow.py` alongside. With `--slow-cars` above 0 the stretch is meant to hold only this
script's cars, and the script warns if it finds other vehicles. `--slow-cars 0` falls back to TM background
traffic from `traffic_flow.py`.

Options: `--slow-cars 12 --slow-kmh 50 80 --speeders 8 --speed-tiers 1.03 1.2 1.4 1.6 --stoppers 0
--shoulder-stoppers 0 --hold-s 40 --cooldown-s 60 --center -10 -10 --radius 210 --heading 90 --seed 7 --keep
--debug`. To add B1 and B2 cars: `--stoppers 2 --shoulder-stoppers 2`.
Ctrl+C ends the run early, and the report is still written.

Every 15 s the status line shows vehicles, E1 count, collisions, cars waiting to be recycled, the loop rate (Hz)
and the longest gap between control updates.

Results go to `ml/data/results/highway_violations/<timestamp>/`:

| File | Contents |
|---|---|
| `report.md` | Planted cars (with a speed-tier table, driver lane-change event counts and E1 per minute), then background traffic, then collisions |
| `violations.csv` | Every event with its status |
| `collisions.csv` | Every contact, with both car ids |
| `tracks.csv` | Every car at 10 Hz |
| `summary.json` | Counts, tiers, driver stats and run settings |
| `debug.csv` | Only with `--debug`: every driver decision at 10 Hz (speed, target, bend cap, lead, lane change, steering, throttle, brake) |

The speed-tier table lists, per tier, the target speed, cars, passes, E1 events, the highest km/h reached and
collisions.

## How cars are judged

The script uses the same thresholds as `ml/violation_engine/rules.py` and the same lane properties as the
engine's Town04 scene:

- **E1:** speed above the limit plus a tolerance for at least 1/3 s, once the car is at least 1 s old. The
  tolerance is 5 km/h below 100 km/h and 5% above. A recycled car starts a new pass: its open episode ends and
  the 1 s age check starts again.
- **B1:** below 5 km/h for at least 20 s on a highway lane, not at a red light.
- **B2:** the same stop with at least half of it on the shoulder. Like the engine, it gets the status
  `possible_breakdown`.

Two kinds of stop are not counted:

- `queue_suppressed`: at least half of the stop was spent behind another stopped car.
- `after_crash`: the stop began within 10 s of that car's collision. A collision sensor sits on every car.

## CarlaAir problems found

### 2026-10-11

1. **Epic quality runs at about 16 fps and Medium at about 47 fps** with traffic. The TM could not steer
   at speed at 16 fps.
2. **The TM is only stable up to about 60 km/h.** With `set_desired_speed` above that, its steering hit full lock
   and every planted speeder ended up in the guardrail. That is why the speeders use their own driver: pure
   pursuit, with at most 0.5 m lane error at 110 km/h on road 45's arc.
3. **TM cars only ever see a 30 km/h limit.** `get_speed_limit()` returns 30 and background traffic drives at a
   median of 20 km/h, so the 90 km/h signs do not reach the TM.
4. **`vehicle_lane_offset` does nothing.** A car with an offset of 3.0 m stayed at 0.00 m, so B2 is steered by hand.
5. **`keep_slow_lane_rule_percentage` raises `rpc_error`** and is skipped.
6. **Launching a TM car with `set_target_velocity` at spawn** left it steering at full lock.
7. **Scene lane ids carry `_p0`/`_p1` suffixes** where `export_lane_map.py` splits a lane. A lookup by
   road/section/lane has to pick the nearest piece. An exact-id lookup missed half of road 45.
8. **"Same lane" must come from the map, not from the heading line.** On road 45's arc, a car 40 m ahead in the
   same lane sits 2.7 m off the heading line. The geometric test missed real lead cars, and the speeders hit them.
9. **Background TM traffic crashes on its own.** It scrapes guardrails and piles up at the merges at both ends
   of road 45, even with no planted cars nearby. So the report lists background "violations" separately and
   treats them as noise.

### 2026-10-14

10. **Fork choice preferred ramps.** `Scene.no_stopping()` is True for ramp lanes (tagged ramp "link") and False
    for mainline junction lanes, so `pursue()` and `curve_cap` followed exit ramps r779 / r1076 off lane 6. Slow
    cars left the highway or spun out, and lane-6 speeders braked to 17 km/h for the ramp bend. Fixed with
    `mainline()`: not a ramp or non-highway lane, then the same lane id, then the least turn.
11. **`get_waypoint()` near an exit gore returns the ramp lane** for a car still in lane 6. Cars then followed the
    ramp, and its 30 km/h scene limit gave false background E1 events. Fixed with `HandDriver.track()`: keep
    advancing the last mainline waypoint while the car is within 3 m of it.
12. **Every vehicle spawn (car + collision sensor) froze CarlaAir for about 0.6 s.** With respawning there were 182
    spawns in 3 min, and all 65 loop gaps over 0.5 s coincided with a spawn. Every car drove blind through each
    freeze: swerves, spin-outs, 38 collisions in 3 min. Fixed by recycling (teleporting) cars instead of
    respawning them, and spawning everything up front.
13. **Lazily filled lane caches (`path_keys`, `curve_cap`) also stalled the loop.** They are now warmed at start
    (`warm_caches`, about 0.5 s for about 4000 waypoints).
14. **Too many slow cars block the speeders.** 18-20 slow cars spread over all four lanes held the speeders
    behind them 85% of the time (median 40-49 km/h on the straight for every tier). Now 12 by default, keeping
    right, mostly in the two outer lanes.
15. **Two cars recycled in the same sample were put on the same spot.** A teleport only shows in the next
    tick's positions, so the second spot search still saw the first car at its old place. Run 6 had 3 such
    overlaps, each starting a pile-up (17 of its 19 contacts). Fixed: one recycle per 0.1 s sample.
16. **A slow car that got onto a ramp wandered the crossing roads inside the radius** for the whole run, never
    recycled. Fixed: a car off the stretch's lanes for 2 s is recycled.
17. **The 1.05 x negative tier (94.5 km/h) overshot to 95.0-96.4 km/h** and was flagged 11 times in run 6. The
    negative tier is now 1.03 x (93 km/h).

## Results

### East straight (2026-10-14, 3 min runs unless stated)

| Run | Change | Contacts | E1 | Notes |
|---|---|---|---|---|
| 1 | First run on the east straight | 14 | 7 | Ramp bug (problem 10) |
| 2 | Ramp fix | 18 | 12 | |
| 4 | Respawning cars | 38 | - | Spawn stalls (problem 12) |
| 5 | Recycling, warm caches | 0 | 8 (2.7/min) | Loop 52 Hz, longest gap 0.04 s. Only 2 of 8 speeders placed. Top speed 113 km/h for the 1.4x tier |
| 6 (5 min) | Violators placed first; 12 slow cars, kept right; best launch spot | 19 | 110 (22/min) | All 8 speeders launched. 0 contacts for the first 211 s, then 3 same-spot recycles (problem 15). 1.05 x negative flagged 11 times (problem 17) |
| **7 (5 min)** | One recycle per sample; stray recycle; negative tier 1.03 x | **1** | **115 (23/min)** | **Current setup.** The one contact: a 1.6 x speeder on the guardrail. 0 background events, loop 53 Hz, longest gap 0.04 s |

### Run 7 (current setup, `20261014_194154`)

| Speed tier | Target | Passes | E1 events | Highest km/h | Collisions |
|---|---|---|---|---|---|
| 1.03 x (negative) | 93 km/h | 39 | 0 | - | 0 |
| 1.20 x | 108 km/h | 39 | 38 | 110 | 0 |
| 1.40 x | 126 km/h | 39 | 39 | 126 | 0 |
| 1.60 x | 144 km/h | 40 | 38 | 128 | 1 |

Compared with the old road-45 setup (below): E1 went from about 4 to 23 per minute, and contacts from about 40
to 1 per 5 min. Speeders passed slow cars 32 times and followed them otherwise (2.5 s + 15 m gap).

### Road 45 arc, TM background traffic (2026-10-11, runs 10 and 11, 5 min each, historical)

These runs used the old setup: road 45 (`--center -311.6 436 --radius 300` for `traffic_flow.py`), 30 TM
background cars from `traffic_flow.py`, 6 speeders at 1.2 x the limit, 2 B1 and 2 B2 cars, and respawning
instead of recycling. With 64 TM cars there were 641 contacts in 5 min, and with 30 about 290.

| Rule | Confirmed events | Notes |
|---|---|---|
| E1 | 21 and 18 | 96-106 km/h in the 90 zone |
| B1 | 3 and 1 | Another 1 stop per run was hit by a TM car (`after_crash`) |
| B2 | 1 and 1 | `possible_breakdown`. Another 1 stop per run was hit by a TM car |

- Planted cars hitting the guardrail: down from 35 to 1 once the script drove them instead of the TM.
- Speeders more than 1 m off their lane centre: down from 25% to 10% of the time after adding the cross-track
  correction.
- Background cars produced about 50 B2 and 15 B1 "violations" per run. They were TM artifacts and are listed
  separately in the report.
- Speeders had about 40 contacts per run with TM cars, which changed lanes into them. This is why the
  background is now scripted.

### B1 stoppers with the speeders (2026-10-14, 10 min runs)

Run with `python highway_violations.py --stoppers 2 --duration 600`. A B1 car cruises at 63 km/h, brakes to rest
in its lane, holds 40 s with hazards on, then drives on. The speeders and slow cars pass it through a free lane,
using the same lane-change logic as for slow cars. No new driving code was needed.

Changes made while testing:

| Change | Why |
|---|---|
| A stop needs all of the next 80 m to be no-stopping highway (checked every 10 m), not only the point 80 m ahead | 2 of 6 stops came to rest on `r775`, the mainline's junction piece at the exit fork (y about -90). The engine does not count junction lanes as no-stopping, so those stops could never be B1 |
| Throttle cuts out 0.5 m/s above the target, not 1.0 m/s | Cars speeding back up after a slow lead overshot by up to 2.6 km/h. The 1.03 x tier (2.3 km/h under the E1 trigger) was flagged twice in 5 min. Without stoppers it already peaked at 95.1 km/h (`20261014_194940`) |
| A stop needs the stretch to go on 150 m past it (`STOP_END_CLEAR_M`), so stops stay at y -38..-6 | Stops at y 160-210 were hit by weaving cars (2 stopped cars hit in `20261014_202444`) |

Tried and reverted: no lane change within a junction's reach (cars could no longer pass a stop just past
`r1073`, and stuck speeders went from 0 to 8); a speed-based steering cap (no change in weaving); a ramp on the
target speed after a lead vanishes (always on: E1 down 25%; only after a loss: no clear gain).

| Run | Setup | B1 stops confirmed | Cars past each stop | Contacts | E1 | 1.03 x tier max |
|---|---|---|---|---|---|---|
| `20261014_205119` | No stoppers (regression) | - | - | 0 | 214 | 94.6 km/h |
| `20261014_212421` | 2 stoppers, final setup | 10 of 10 | 7-19, at 3.5 m (one lane) | 5 | 212 | 94.6 km/h |

Earlier runs with stoppers (the same setup without the 150 m rule, or with one of the reverted tries) had 0, 2,
4 and 4 contacts in 10 min. Without stoppers: 1, 0 and 0 in 5 + 10 + 10 min.

**Contacts with stoppers on are caused by weaving, not by the stops.** In the final run, all 5 contacts were at
y 117-168, far from the stops, and no stopped car was hit:

- At the north end (y > 80), cars at 100+ km/h weave by up to 3 m. This also happens without stoppers (4-5% of
  samples at 100-120 km/h).
- Two things start it. A lead recycled away from the end vanishes, the target jumps and the throttle goes to
  1.0. A neighbour weaving more than 1 m off its lane centre counts as a lead in the next lane (`lane_cars`),
  which brakes the follower hard at 120 km/h and spreads the weave.
- Stoppers bunch the traffic, so a weaving car more often has someone next to it.
- Contacts are listed in `collisions.csv`, and a stop hit by a car would be marked `after_crash` and not
  counted, so the B1 results stay clean.

## Still open

- **North-end weave** (see the B1 results above): the following and steering loops need damping at 100+ km/h,
  and the `lane_cars` rule for straddling cars may need tightening. This affects speeders-only runs too, but
  only shows as contacts when other cars are near.
- A car that slips onto exit ramp `r1076` (30 km/h) can be flagged E1 before the stray check recycles it
  (once in `20261014_211238`, a 1.03 x car at 44 km/h).
- The 1.6 x tier tops out at about 128 km/h, not 144: the straight is too short to reach it between slow cars
  and the bend braking that starts near y 30.
- The lane-change counts show far more "return" changes started than finished (96 vs 2 for speeders in run 7).
  Most are probably cut short by the recycle at the end of the stretch, which resets the driver without
  counting. Not checked.
- B2 (shoulder stops) has not been rerun on the east straight with scripted traffic. B1 has (above).
- The engine (`run_violations.py --oracle`) has not been run on this yet. That needs a recorded flight over the
  stretch.
