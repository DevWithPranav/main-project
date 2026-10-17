# What-If Recommendation System: Plan

Status: plan, 2026-10-10. Extends Build_Plan **M8** (recommendation system) from "recommendations from event data" to "a planner edits the twin and gets an impact analysis". Approach decided with the team: **formulas first, CARLA validation second, no trained model.**

## 1. Goal

A city planner changes the digital twin (signals, speed limits, density, props, rules, closures, incidents) and asks **"what happens if we make this change?"** The system returns:

1. before/after comparison
2. predicted impact (congestion, flow, travel time, safety, violations)
3. best case and worst case
4. alternatives, scored and compared
5. an explanation of why
6. a way to validate in the simulator before anything is applied to the real system

## 2. What already exists (do not rebuild)

| Piece | Where |
|---|---|
| Recommendation engine: grouping, rules, priority, confidence, limitations, alternatives, validation plan, projected estimate (`sim_*`) | [ml/planning/recommend.py](../ml/planning/recommend.py) |
| Analytics, action catalogue, planner history | `ml/planning/analytics.py`, `catalogue.py`, `history.py` |
| API: `/recommendations`, decision, `/planner/history` | [backend/app/routers/planning.py](../backend/app/routers/planning.py) |
| UI | [frontend/src/pages/Recommendations.tsx](../frontend/src/pages/Recommendations.tsx) |
| Baseline-vs-modified runner (started) | [simulation/planning/validate_recommendation.py](../simulation/planning/validate_recommendation.py) |
| Twin + planner edits (M7) | `twin/` |

The existing engine is **data-driven** (events in, recommendations out). The new work is **action-driven** (edit in, impact out). The two share the same output schema so the UI treats both alike.

## 3. Gaps

| Spec item | Gap |
|---|---|
| Action-based feedback | Twin edits do not trigger analysis. |
| Before/after | Needs reproducible CARLA runs (seed, synchronous mode); see Research_Notes "Reproducible CARLA runs". |
| Impact prediction | Only 5 action types in `sim_*`; no congestion, travel-time or safety model. |
| Best/worst case | Single point estimate, no range. |
| Alternatives | Listed, not scored or compared. |
| Simulate before apply | No draft-change object; no validate-then-commit step. |
| Explainability | Evidence and limitations exist; per-factor contribution does not. |

## 4. Design

### 4.1 One action type

```
PlannerAction {
  id, type, target (road_id | lane_id | junction_id | point | polygon),
  params {...}, author, created_at
}
types: signal_timing | speed_limit | traffic_density | lane_closure | road_closure
     | prop_add | prop_remove | prop_move | rule_change | incident | scenario
```

A **Draft** is a list of `PlannerAction` applied to a copy of the scene. The live scene is untouched until the planner commits a validated draft. Twin, backend and engine share the schema (one JSON Schema file, validated in both Python and TypeScript).

### 4.2 Baseline state

Built from what we have measured, per road section / junction approach:

- flow `q` (veh/h) and density from trajectories
- mean speed and free-flow speed (85th-percentile or limit)
- queue length, stop count (from kinematics)
- violation counts per type and location (events)
- geometry: lanes, lengths, signal phases (lane map JSON, site config)

Anything not measurable from the footage (for example saturation flow, signal cycle) comes from the site config and is labelled **assumed**, with the assumption shown to the planner.

### 4.3 Tier 1: formula predictor (instant)

Published, explainable formulas. Each returns an **expected** value and a **low/high** range (best/worst case) by varying its uncertain inputs across a stated interval.

| Effect | Formula | Used for | Source |
|---|---|---|---|
| Signal delay | HCM control delay `d = d1·PF + d2 + d3`; `d1` is the Webster-derived uniform term, `d2` incremental delay for random arrivals and oversaturation | signal timing, green split, cycle length, adding or removing a signal | [Webster/HCM delay](https://archive.nptel.ac.in/content/storage2/courses/105104098/TransportationII/lecture8/text/8%20slide.htm) |
| Link travel time | BPR `t = t0·(1 + α(v/c)^β)`, α=0.15, β=4 default; capacity `c` scaled by lanes lost; `t0` raised for work-zone speed | lane or road closure, speed limit, density | [BPR / Visum VD functions](https://cgi.ptvgroup.com/vision-help/VISUM_2025_ENG/Content/1_Benutzermodell%20IV/1_5_Vordefinierte%20CR-Funktionen.htm) |
| Blocked-road correction | Standard BPR underestimates delay on partly blocked roads; widen the high end of the range and flag it | closure, incident | [UGPTI](https://www.ugpti.org/resources/reports/downloads/mpc22-448-brief.pdf) |
| Crash change from speed | Power model: `crashes_after = crashes_before · (v_after/v_before)^k`; k from Elvik's revised, lower, road-type-specific exponents. Original Nilsson values (2 injury, 3 serious, 4 fatal) are rural-based and **not valid for urban arterials** | speed-limit change | [Cameron & Elvik](https://www.science.gov/topicpages/n/nilsson+model.html), [SWOV](https://swov.nl/nl/publicatie/nilssons-power-model-connecting-speed-and-road-trauma-does-it-apply-urban-roads) |
| Conflict risk | TTC and PET from simulated trajectories; report TET/TIT. Thresholds vary widely across studies (TTC 1.5 to 4 s; PET 1 to 8 s), so thresholds are explicit parameters and results are shown for a range | safety before/after | [FHWA SSAM](https://www.fhwa.dot.gov/publications/research/safety/03050/02.cfm) |
| Violations | Existing event rates per location; action-specific effect modifiers come from the catalogue and carry a **source or "assumed"** tag | rule changes, props (signs, barriers) | `ml/planning/catalogue.py` |

Rules for the predictor:

- Every number shown is tagged **measured** (from our footage or runs), **formula** (computed from a published model on measured inputs), or **assumed** (a parameter we set). Published coefficients are the papers' values, not ours (rule 3); where we have data we calibrate and report our own fit.
- Unsupported action types return "no estimate" with the reason, never a guess.
- A static formula cannot capture demand response (traffic may drop or reroute after a closure; see the [York closure study](https://www.richardclegg.org/previous/pubs/rgc_utsg2006.doc)). This goes in the limitations of every closure result.

### 4.4 Tier 2: CARLA validation (slow, authoritative)

Same scenario as baseline and as modified: same town, traffic seed, vehicle count, weather, duration, synchronous mode, fixed time step, Traffic Manager seed. The Tier 2 result **replaces** the Tier 1 estimate on screen and the gap between them is logged.

Metrics per run: violation counts by type, TTC/PET conflicts, mean speed, travel time over a fixed route set, queue length, throughput. Run N seeds (target 3 to 5) and report mean and spread, not one run.

### 4.5 Alternatives

For a draft action, generate variants by perturbing its parameters inside bounds (for example green split ±10 %, limit ±10 km/h, one lane vs two) plus the related catalogue actions. Score each with Tier 1. Rank by a transparent weighted objective (delay, conflict risk, violations, side effects) with weights the planner can change. Show a table: option, predicted change per metric, low/high, pros, cons, confidence.

### 4.6 Explainability

Each result carries:

- the formula used and its inputs (with measured / formula / assumed tags)
- per-factor contribution to the predicted change (for example "delay +14 s: capacity −25 % (+11 s), speed limit −10 km/h (+3 s)", *illustrative*)
- evidence events and locations
- limitations and what would change the answer
- the history of past validated outcomes for the same action type, once available

## 5. API

```
POST /whatif            body: {scene_id, actions[]}  -> {draft_id, baseline, predicted{expected,low,high}, alternatives[], explanation}
GET  /whatif/{draft_id}                                 current state + validation status
POST /whatif/{draft_id}/validate                        queue the CARLA baseline-vs-modified run
GET  /whatif/{draft_id}/validation                      measured comparison when done
POST /whatif/{draft_id}/commit                          only if validated; writes the change + planner history
```

Live updates reuse the existing WebSocket fan-out. Role permissions follow `frontend/src/lib/permissions.ts` (planner can draft and validate; commit needs planner or admin).

## 6. UI (twin + Recommendations page)

- Edit panel: any `PlannerAction` can be created by direct manipulation on the twin.
- **Impact panel** updates on each edit: before/after bars per metric, a low/high band (best/worst case), the tags, and the "why".
- **Alternatives table** with "try this instead".
- **Validate in sim** button, with run status and the measured result replacing the estimate.
- **Apply** is disabled until validated; everything lands in planner history.

## 7. Phases (deadline Sun 2026-10-11 22:00; rehearsal Monday)

Order is by dependency. Phases 0 to 3 are the must-have demo; later phases are stretch.

| # | Phase | Output | Done when |
|---|---|---|---|
| 0 | Reproducible traffic | `traffic_flow.py --seed`, synchronous mode, TM seed | two runs with the same seed give identical vehicle counts and metrics (measured) |
| 1 | Action schema + draft model | JSON Schema, Python/TS types, draft store | schema validates in both languages; tests pass |
| 2 | Tier 1 predictor, signals + speed + closure | `ml/planning/whatif.py`, unit tests per formula | known textbook cases reproduce; results include low/high and tags |
| 3 | `/whatif` + minimal UI panel | endpoint, impact panel | edit a signal or speed limit in the twin and see before/after with a range |
| 4 | CARLA validation runner on drafts | baseline vs modified, N seeds, TTC/PET | one action validated end to end with measured metrics |
| 5 | Alternatives + ranking | scored table | at least 2 alternatives with pros/cons for each supported action |
| 6 | Props and rules, scenarios | `prop_*`, `rule_change`, `incident`, `scenario` effects | each type either has a sourced estimate or returns "no estimate" |
| 7 | Explainability polish + history ranking | contribution breakdown; rank by measured outcomes | planner history changes the ordering of proposals |
| 8 | Evaluation | predicted vs CARLA-measured error per action type | table with our own numbers for the M10 report |

## 8. Evaluation (what we will report)

- **Prediction error:** Tier 1 expected vs Tier 2 measured, per action type and metric. Does the CARLA result fall inside the low/high band? Report the fraction (from our runs only).
- **Ranking quality:** does the top alternative by Tier 1 also win in CARLA?
- **Determinism:** seed-to-seed spread of the baseline.
- **Latency:** Tier 1 response time; Tier 2 run time on the RTX 4050 laptop.
- **Planner-facing:** every result has tags, limitations, and a validation path.

## 9. Risks

| Risk | Handling |
|---|---|
| CARLA is not real traffic; the twin may underestimate real conflicts (as simulation did for illegal pedestrian crossings in published work) | state in limitations; never claim real-world accuracy from CARLA alone |
| TTC/PET thresholds change results | explicit parameters, run over a threshold range |
| Published coefficients may not fit our sites (urban, drone footage, different country) | calibrate where our data allows; otherwise label as published and uncalibrated |
| Static formulas ignore rerouting and demand response | stated limitation; Tier 2 shows local effect only |
| Short time to deadline | phases 0 to 3 first; later phases are additive |
| Simulator needed for phases 0, 4 | say so; do not fake those runs |

## 10. Future work (out of scope now)

- Surrogate model trained on many seeded CARLA runs (needs hundreds of runs from phase 0).
- LLM layer to turn natural-language questions into `PlannerAction` and to phrase explanations. It must never produce the numbers.
- Network-level assignment with demand response.
- Calibration against real counts from the datasets once site data allows.

## 11. Research log

Sources and what was adopted are in [Research_Notes.md](Research_Notes.md), section "What-if recommendation system (2026-10-10)". Per rule 6, an idea is adopted into the code only after it is tested here.
