# Interactive Traffic Scenario Builder: plan and status

Requested 2026-10-11 (replaces the twin's What-if section). Core idea: right-click the twin map, add props / rules /
scenarios, see the impact, test alternatives, compare, and export an evidence-based report.

## 1. Rules for the numbers (project rule 3)

Every number shown is one of four kinds and is labelled so in the UI and the report:

| Label | Where it comes from | Available now |
|---|---|---|
| **observed** | recorded traffic of a session (before the change) | yes: speed, density, queue, travel time, conflicts |
| **replay (measured)** | the violation engine re-run on the same recorded traffic with the change applied (`ml/planning/whatif.py`). Drivers keep driving as recorded: their response is not modelled | yes: violations before / after |
| **virtual simulation** | the built-in virtual traffic simulator (`ml/planning/microsim.py`): vehicles on the lane map, baseline vs plan on the same seeds, mean ± 95 % interval. A simplified model, **not CARLA**, not calibrated to real traffic | yes (added 2026-10-11) |
| **simulated (measured)** | CARLA baseline vs modified runs, several seeds, 95 % interval (`simulation/planning/validate_recommendation.py`, `sim_metrics.py`) | needs the simulator PC (CarlaAir) |
| **projected / rule of thumb** | catalogue factors with a cited source, or heuristic risk checks | yes, always marked "projected" / "not measured" |

Anything that needs a simulation and has none yet shows "not simulated yet", never an estimate dressed as a result.

## 2. Phases

| Phase | Content | Status |
|---|---|---|
| 1a | What-if UI removed; plan data model, catalogue + support matrix, validation + conflicts, location lookup, versioned storage, API | in progress |
| 1b | Right-click menu, builder panel (props / rules / scenarios / templates), items on the 3D map, undo / redo, recommendation side panel (validation, observed baseline, replay before / after, best / worst from replay, rule-based alternatives), bottom simulation bar | done and browser-tested 2026-10-11 |
| 1c | Report (PDF / XLSX / CSV, sources kept apart, screenshot, list of earlier reports, reopen the scenario); plan comparison on the same traffic (violations only) | report done; objective-weighted ranking not built |
| 1d | **Virtual traffic** so added vehicles move: spawn group, continuous flow, demand factor, jam, accident, breakdown, closures, speed limits, bus / emergency lanes; baseline vs plan over several seeds with intervals; the paths play in the twin | done 2026-10-11 (simplified model, listed limits below) |
| 2 | CARLA execution: compile a plan to props / traffic manager / signal timing / spawned traffic, async job + progress, seeds, 95 % intervals, heatmaps, baseline vs modified on all requested metrics | needs the simulator; not started |
| 3 | Natural-language scenario creation (needs an LLM API key and a decision on where data goes), AI-suggested experiments, sharing / comments / approval workflow, multi-location propagation | not started |

## 3. Data model (one JSON document per plan version)

```
plan: { name, description, scene (CARLA town), session_id (traffic to replay, optional), objectives[],
        props[]:     { id, kind, x, y, yaw_deg, params{}, schedule{from_s,to_s}?, note }
        rules[]:     { id, kind, target{lane_glob | polygon | center+radius_m}, params{}, priority, schedule?, note }
        scenarios[]: { id, kind, x, y, radius_m?, params{}, schedule?, note } }
```

Plans are stored in PostGIS tables `plans` + `plan_versions` (every save = a new version with who / when / note), so history and
"restore previous" come from the versions. Kinds, their parameters and how each is evaluated come from one catalogue
(`backend/app/plan_catalogue.py`), served by `GET /api/plans/catalogue`; the UI builds its forms from it.

## 4. What each element can do (support matrix)

"Replay" = changes what the violation engine judges on recorded traffic. "CARLA" = what the simulator can do (Phase 2);
blueprint names are from memory of the CARLA blueprint library and must be checked on the CarlaAir build (`list_blueprints`).

| Element | Replay (now) | CARLA (Phase 2) |
|---|---|---|
| Speed limit (road / lane) | yes: `lane_overrides.speed_limit_kmh` | yes: traffic-manager target speed |
| Speed limit sign (prop) | as speed limit on the nearest lanes | visual prop + traffic manager |
| No-parking / parking zone | yes: `no_parking` zone (+ grace) | rule-side only (zone) |
| Crosswalk / speed breaker | crosswalk zone yes; breaker not evaluated | crosswalk props; breaker approximated by a speed cap |
| Lane restriction (bus / emergency / restricted) | yes: `lane_overrides.restricted` | traffic manager lane rules, approximated |
| One-way | yes: `lane_overrides.one_way` | approximation (cannot change OpenDRIVE live) |
| Road closure / construction zone / barriers / cones | approximated as a restricted lane for the replay (vehicles inside are flagged) | static props + blocked lane; traffic manager reroutes only through spawned detours |
| Signal timing change | not evaluated | yes: `TrafficLight.set_green_time` etc. |
| New traffic signal / adaptive signal | not evaluated | not creatable at run time: needs an OpenDRIVE signal in a regenerated map; approximated by a stop-and-go virtual signal |
| Stop sign | not evaluated | approximated (virtual stop line) |
| Bus stop / bus lane | lane restriction for the bus lane; the stop itself is not evaluated | props + restricted lane |
| Emergency priority lane / vehicle | lane restriction | emergency vehicle with lights / sirens, other vehicles yield only approximately |
| Time-based restriction | applied for the window where the engine rule supports it | scheduled on / off |
| Vehicle spawns, congestion, surge, accident, breakdown, pedestrians | not evaluated (no recorded traffic for hypothetical vehicles) | yes (spawn, route, speeds, density) |
| Weather | not evaluated | yes (weather parameters) |

Unsupported or approximated elements are accepted into a plan (so planners can record intent) but are listed under
"not evaluated by replay" with the reason; they never produce numbers.

## 5. Validation and conflicts

- Schema: kinds known, parameters in range, positions inside the scene's bounds.
- Placement: road props within 8 m of a lane; no two props closer than 1 m; rule targets must match lanes.
- Conflicts: two different speed limits on the same lane with overlapping schedules (higher priority wins, equal priority = error);
  one-way vs restricted contradictions; a no-parking zone over a bus stop; a closure of the only lane that a bus / emergency lane
  rule needs; a plan rule that contradicts a map or profile rule on the same lane (reported, the plan wins by priority).

## 6. Impact analysis (what the side panel shows)

1. **Validation** result.
2. **Observed baseline** around the change from the session's recorded traffic: mean / 85th-percentile speed, vehicles, density,
   queue length, travel time through the area, TTC conflicts (`simulation/planning/sim_metrics.py` on the recorded rows).
3. **Replay before / after**: counted violations by type, events removed (best case among recorded movements) and events added
   (worst case: new violations the change creates on the same traffic).
4. **Rule-of-thumb risk checks** (marked "not measured"): e.g. speed limit cut by more than 30 km/h, closure next to a junction,
   new signal within 100 m of another, no-parking zone next to a crosswalk.
5. **Alternatives** with expected benefits, risks, trade-offs and the evidence level: *preliminary* (rule of thumb),
   *replay-validated*, *simulation-validated* (Phase 2), or *insufficient data*.
6. Metrics that need a simulation (travel time after, throughput, queue after, delay, utilisation, safety indicators) show
   "not simulated yet" until a CARLA run exists.

## 7. Virtual traffic simulator (`ml/planning/microsim.py`)

What it is: a small microscopic model so that vehicles a planner adds actually drive, and so a plan can be compared with the
baseline without CarlaAir. **It is not CARLA** and says so wherever its numbers appear.

- Lanes and successor links of the scene; one vehicle follows one lane at a time; Intelligent Driver Model for following
  (Treiber et al. 2000); desired speed = lane limit x a per-vehicle factor, capped by the vehicle type; random-walk or
  shortest-path routes avoiding closed lanes; types car, van, truck, bus, motorcycle, ambulance / police / fire.
- Background traffic keeps a constant number of vehicles per km of lane (7 per km by default); demand scenarios add vehicles
  inside their radius. Plan items act through the compiled lane overrides (speed limits, closed / reserved lanes) and the
  scenarios (spawn group, continuous flow, jam, accident, breakdown, emergency vehicle, weather speed factor, speed breaker cap).
- Not modelled: lane changes and overtaking, junction conflicts (vehicles pass through each other inside junctions), traffic
  signals and their timing, pedestrians, bus-stop dwell, diversions. Elements that depend on these are listed as "not simulated".
- Runs: 5 seeds x (baseline + plan), 120 s after a 20 s warm-up, reproducible (`numpy.random.RandomState(seed)`); per metric the mean
  and 95 % interval (Student t); "different" only when the intervals are apart, else "no difference detected". Metrics (inside the
  area around the plan): average and 85th-percentile speed, vehicles, density, throughput, travel time through the area, queue length,
  share of vehicle-time stopped, delay (time lost against driving at the lane limit).
- Time-to-collision conflicts were left out on purpose: without junction conflicts they would be meaningless.
- Measured speed: about 1 s per run on an idle PC (Town05, about 120 vehicles); the API reports progress per run (the builder shows a
  bar). Not validated against CARLA or real traffic: the numbers show the model's response to the plan, not a forecast.
