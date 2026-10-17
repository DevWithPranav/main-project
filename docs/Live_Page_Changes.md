# Live monitoring page: changes log

File: `frontend/src/pages/Live.tsx`

## 2026-10-14: hide the Map and Events panels

Nothing was deleted. The Map and Events panels are hidden, and their code, queries and state are all still there.

### Hidden (kept in the code)

| Section | What it is | How it is hidden |
|---|---|---|
| **Map card** | `Card` with the `LaneMap` (lanes, zones, vehicles, trails, event pins) | The `<Grid>` that holds it has `hidden={!SHOW_MAP_AND_EVENTS}` |
| **Events card** | `Card` with the "Events (N)" list that opens `EventDrawer` | Same `<Grid>` |

To bring them back, set `SHOW_MAP_AND_EVENTS = true` (a constant near the top of `Live.tsx`).

The queries and state that feed these panels (trajectories, events, zones, live WebSocket) still run, because the replay clock and the videos use them.

### Changed (moved)

| Section | Before | After |
|---|---|---|
| **Replay transport** (back-to-start button, play/pause, time slider, `t / tMax` readout, speed select, "loading trajectories…" note) | Inside the Map card, under the map | Its own `Card` placed above the hidden `Grid`, shown in Replay mode only |

**Why it was moved:** the transport drives the replay clock `t`, and both videos follow that clock. If it stayed inside the hidden Map card, Replay could no longer be played or scrubbed. Its behaviour and handlers are unchanged. Only its position changed, and it now sits in a `Card` of its own.

### Added

- `const SHOW_MAP_AND_EVENTS = false;` (the switch)
- Two comments explaining the switch and the moved transport.

### Not changed

Page header, health badges, session / town / mode selectors, the "live pipeline" alert, the two video panels (camera footage and model output), `EventDrawer`, and all hooks, queries and state.

### Deleted

None.

---

# Statistics page: hidden

File: `frontend/src/App.tsx`

## 2026-10-14: hide the Statistics page

Nothing was deleted. `pages/Stats.tsx` and its import are untouched.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **"Statistics" side-menu entry** (`NAV` item `/stats`) | Added to `NAV` only when `SHOW_STATS` is true |
| **`/stats` route** (`<StatsPage />`) | Rendered only when `SHOW_STATS` is true. While hidden, `/stats` falls through to the catch-all and redirects to `/live` |

To bring it back, set `SHOW_STATS = true` (a constant above `NAV` in `App.tsx`).

### Added

- `const SHOW_STATS = false;`

### Deleted

None.

---

# 3D digital twin: Planner panel, "Selected lane" editor hidden

File: `frontend/src/twin/TwinView.ts`

## 2026-10-14: hide the lane editor (the Planner panel itself stays)

Nothing was deleted. An earlier version of this change hid the whole Planner panel; that was reverted. Only the lane editor is hidden now.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **"Selected lane" editor** (`#laneEdit`): road class, lane type, speed limit, lane change allowed, one-way, bridge, tunnel, ramp, median, restricted lane, "whole road direction", **Add lane change** | `select()` shows it only when `SHOW_LANE_EDIT` is true |

To bring it back, set `SHOW_LANE_EDIT = false` to `true` (constant near the top of `TwinView.ts`).

### Changed

| Item | Before | After |
|---|---|---|
| Planner hint when a lane is clicked | Hint hidden, lane editor shown | Hint hidden, lane editor hidden: selecting a road shows nothing in the Planner (the lane details still appear in the Selected info box) |

### Still shown

The Planner panel and its toggle, the "New zone" section, pending edits / save, edit history, the right-click map menu ("Add Custom Rules" scenario builder) and the "Custom traffic" switch.

### Added

- `const SHOW_LANE_EDIT = false;`

### Deleted

None.

---

# Configuration page: "Road configuration" tab hidden

File: `frontend/src/pages/Config.tsx`

## 2026-10-14: hide the Road configuration tab

Nothing was deleted. The `RoadEditor` import and `components/RoadEditor.tsx` are untouched.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **"Road configuration" tab** (`Tabs.Tab value="road"`) | Rendered only when `SHOW_ROAD_TAB` is true |
| **Its panel** (`<RoadEditor />`) | Same condition |

To bring it back, set `SHOW_ROAD_TAB = false` to `true` (constant above the page component in `Config.tsx`).

### Still shown

"Profiles & thresholds" (still the default tab) and "Zones".

### Added

- `const SHOW_ROAD_TAB = false;`

### Deleted

None.

---

# 3D twin scenario builder, Traffic tab: traffic presets reduced to two

File: `frontend/src/twin/builder/trafficUi.ts`

## 2026-10-14: keep only "Normal traffic" and "Heavy traffic"

Under "Traffic character" the Traffic tab offered 18 preset buttons. Only two are shown now.

### Shown

| Button | Backend preset key | Notes |
|---|---|---|
| **Normal traffic** | `moderate` | Backend label is "Moderate traffic" (7 veh/km, the default); the button is relabelled in the UI only |
| **Heavy traffic** | `heavy` | 14 veh/km, more trucks |

### Removed from the UI (backend untouched)

`empty` (Empty roads), `light` (Light traffic), `rush_hour` (Rush hour), `night` (Night), `freight_heavy` (Freight corridor), `event_day` (Event day), `school_run` (School run), `delivery_hours` (Delivery hours), `bus_corridor` (Bus corridor), `two_wheelers` (Two-wheeler heavy), `calm_drivers` (Calm drivers), `aggressive_drivers` (Aggressive drivers), `tourist_weekend` (Tourist weekend), `gridlock` (Gridlock), `ghost_town` (Almost empty).

They are only filtered out of the button row (`SHOWN_PRESETS` in `trafficUi.ts`). `PRESETS` in `ml/planning/traffic_config.py` still defines them, so saved plans using them still load, and the "Traffic scenarios" buttons that refer to them still work. To bring one back, add its key to `SHOWN_PRESETS`.

### Not changed

The "Traffic scenarios" row (Stadium exit, Freight run, ...) and every other Traffic-tab section.

### Deleted

None in the code.

---

# 3D twin scenario builder, Traffic tab: "Traffic scenarios" hidden

File: `frontend/src/twin/builder/trafficUi.ts`

## 2026-10-14: hide the Traffic scenarios section

Nothing was deleted.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **"Traffic scenarios" heading and buttons** (Stadium exit, Freight run, Bus service, Crash at rush hour, Ambulance through traffic, Road works slowdown, Truck convoy, School zone, Night freight, Commuter inflow, ...) | Rendered only when `SHOW_SCENARIOS` is true |
| The hint "Right-click the map and choose Add Custom Rules to place a scenario." | Same condition (it belonged to this section) |

To bring it back, set `SHOW_SCENARIOS = false` to `true` (constant above `trafficTabHtml`). The click handler (`data-tscn` in `PlanBuilder.ts`) and the backend `SCENARIO_PRESETS` are untouched.

### Still shown

"Traffic character" (Normal / Heavy), the schema sections, "Sources, groups and zones", last run, export / import.

### Added

- `const SHOW_SCENARIOS = false;`

### Deleted

None.

---

# Live monitoring page: opens in Live mode, service status tags hidden

File: `frontend/src/pages/Live.tsx`

## 2026-10-15: default to Live, hide the db / redis / s3 tags

Nothing was deleted.

### Changed

| Item | Before | After |
|---|---|---|
| **Replay / Live toggle** default | Replay | **Live** (`useState<'replay' \| 'live'>('live')`). Replay is still one click away |

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **`db`, `redis`, `s3` status tags** in the page header (`Health`, one badge per service from `/api/health`) | Rendered only when `SHOW_SERVICE_BADGES` is true |

To bring them back, set `SHOW_SERVICE_BADGES = false` to `true` (constant near the top of `Live.tsx`).

### Still shown

The "backend unreachable" badge (only when the backend does not answer), the "clips N pending" badge (only while evidence clips are being converted or failed), and the app-wide red alert when the database or Redis is down (`App.tsx`), so an outage is still visible.

### Added

- `const SHOW_SERVICE_BADGES = false;`

### Deleted

None.

---

# Header bar: username and role badge hidden

File: `frontend/src/App.tsx`

## 2026-10-15: hide the "planner" name and the role badge

Nothing was deleted.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **Username** text (e.g. "planner") and the **role badge** (e.g. PLANNER) at the right of the top bar | Rendered only when `SHOW_USER_BADGE` is true |

To bring them back, set `SHOW_USER_BADGE = false` to `true` (constant above `NAV` in `App.tsx`). The role still controls what the user may edit; only its display is hidden.

### Still shown

Dark-mode toggle, Log out, the side-bar toggle and the app title.

### Added

- `const SHOW_USER_BADGE = false;`

### Deleted

None.

---

# Condition labels: id prefix hidden, B1 renamed

Files: `frontend/src/lib/format.ts`, `schemas/conditions.json`

## 2026-10-15: "B1 · Stopping on the main carriageway" → "Stopping on the highway"

Nothing was deleted.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **Condition id prefix** ("B1 · ", "F1 · ", ...) in every condition label: Violations table "Condition" column, the event drawer's Condition row, Statistics (hidden page) | `conditionLabel()` adds it only when `SHOW_CONDITION_IDS` is true |

To bring it back, set `SHOW_CONDITION_IDS = false` to `true` (constant above `conditionLabel` in `format.ts`). The ids themselves are unchanged in the data, the API, filters and exports.

### Changed

| Item | Before | After |
|---|---|---|
| **B1 name** (`schemas/conditions.json`) | Stopping on the main carriageway | Stopping on the highway |

The backend caches `conditions.json` (`schemas.load`), so it shows the new name after a backend restart.

### Added

- `const SHOW_CONDITION_IDS = false;`

### Deleted

None.

---

# Violations page: Type as bold text, Status column hidden

File: `frontend/src/pages/Violations.tsx`

## 2026-10-15: no coloured badge in Type, no Status column

Nothing was deleted.

### Changed

| Item | Before | After |
|---|---|---|
| **Type** cell (e.g. NO PARKING) | Coloured filled badge (`TYPE_COLORS`) | Bold plain text, same label (`typeLabel`). The badge is kept: `TYPE_AS_BADGE = true` brings it back |

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **Status column** (header and the flagged / needs review / ... badge per row) | Rendered only when `SHOW_STATUS_COLUMN` is true; the loading / empty rows span 7 columns instead of 8 (`N_COLS`) |

To bring it back, set `SHOW_STATUS_COLUMN = false` to `true` (constants above the page component in `Violations.tsx`).

### Still shown

The Review column, the status filter in the filter bar, and the status in the event drawer (opened by clicking a row).

### Added

- `const TYPE_AS_BADGE = false;`, `const SHOW_STATUS_COLUMN = false;`, `const N_COLS`

### Deleted

None.

---

# Event drawer (Violations / Live): pills, dwell / grace, tags and Review hidden

File: `frontend/src/components/EventDrawer.tsx`

## 2026-10-15: show the confidence only

Nothing was deleted.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **Pills under the title**: type (NO PARKING), status (FLAGGED), conf, session (LIVE_LOCAL_2) | Rendered only when `SHOW_HEADER_BADGES` is true; otherwise one plain line "Confidence 0.96" |
| **dwell s** and **grace s** rows (the stop rules' `value`) | Keys in `HIDDEN_VALUE_KEYS` are skipped; other values (e.g. a speeding event's speed) still show |
| **Tags** (e.g. ID_SWITCH_BRIDGED) | Rendered only when `SHOW_TAGS` is true |
| **Review section**: the divider, "Not reviewed yet." / the last review, the note box and Confirm / Dismiss, "Your role ... can view but not review" | Rendered only when `SHOW_REVIEW` is true |

To bring one back, set its constant to `true` (or empty `HIDDEN_VALUE_KEYS`), at the top of `EventDrawer.tsx`. The review API and permissions are unchanged; with `SHOW_REVIEW` false nobody can confirm / dismiss from the drawer.

### Still shown

The title (event type), the evidence clip / snapshot, Condition, Vehicle, Started, Flagged, Ended, Lane, other values, Position.

### Added

- `SHOW_HEADER_BADGES`, `HIDDEN_VALUE_KEYS`, `SHOW_TAGS`, `SHOW_REVIEW`

### Deleted

None.

---

# Event drawer: no-evidence message reworded

File: `frontend/src/components/EventDrawer.tsx`

## 2026-10-15

| Item | Before | After |
|---|---|---|
| Message when an event has no clip or snapshot | No evidence clip for this event (render_violations.py --clips makes them). | No evidence clip available for this event. |

Deleted: none (text change only).

---

# Sessions page: Source column hidden

File: `frontend/src/pages/Sessions.tsx`

## 2026-10-15

Nothing was deleted.

| Section | How it is hidden |
|---|---|
| **Source column** (header and the per-row badge: carla / video / ...) | Rendered only when `SHOW_SOURCE_COLUMN` is true; the empty-state row spans 6 columns instead of 7 |

To bring it back, set `SHOW_SOURCE_COLUMN = false` to `true` (constant above the page component). The session's source is unchanged in the data and still drives the Live page (map vs site video).

Added: `const SHOW_SOURCE_COLUMN = false;`. Deleted: none.

---

# Live monitoring page: Town04 by default, one violation pop-up, camera subtitle hidden

File: `frontend/src/pages/Live.tsx`

## 2026-10-15

Nothing was deleted.

### Changed

| Item | Before | After |
|---|---|---|
| **Town** for a session without its own town (live sessions) | `?town=`, else the first session that has a town, else the first town | `?town=`, else **Town04** (`DEFAULT_TOWN`), else as before. Sessions that carry their own town still use it |
| **Violation pop-ups** (one per live event, top right, 4 s) | A new pop-up per event, stacking (up to 5 on screen, the rest queued) | **One at a time**: a new event replaces the text of the one on screen (fixed id `VIOLATION_NOTE_ID`, `notifications.update`); closing it (the x, or its 4 s timer) runs `notifications.clean()`, which removes every notification on screen and in the queue |

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **"as received by the detector"** under the camera footage panel (Live mode) | Shown only when `SHOW_RAW_SUBTITLE` is true. The model output panel's line ("detections, track IDs, km/h, rule state") stays |

### Added

- `const DEFAULT_TOWN = 'Town04';`, `const VIOLATION_NOTE_ID = 'live-violation';`, `const SHOW_RAW_SUBTITLE = false;`

### Deleted

None.

---

# Live monitoring page: "rule state" dropped from the model output subtitle

File: `frontend/src/pages/Live.tsx`

## 2026-10-15

| Item | Before | After |
|---|---|---|
| Subtitle of the model output panel (Live mode) | detections, track IDs, km/h, rule state | detections, track IDs, km/h |

Deleted: none (text change only; the boxes are still coloured by rule state on the video).

---

# Configuration page, "Profiles & thresholds": Profile selector, description and History hidden

File: `frontend/src/pages/Config.tsx`

## 2026-10-15

Nothing was deleted.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **Profile** selector (dropdown above the editor) | Rendered only when `SHOW_PROFILE_SELECT` is true. Without it the page edits the profile it picks by itself: `default` (else the first one) |
| **Profile description** line, e.g. "rules.DEFAULTS, all six in-scope types on, red light off" | Rendered only when `SHOW_PROFILE_DESCRIPTION` is true |
| **History** card (saved versions, who, note) | Its grid column has `hidden={!SHOW_PROFILE_HISTORY}`; the editor then takes the full width (lg 12 instead of 8) |

To bring one back, set its constant to `true` (above `ProfileEditor` in `Config.tsx`). Saving still asks for a note and still writes a new version; the history is kept by the backend.

### Still shown

The tabs "Profiles & thresholds" and "Zones", the per-type cards (on / off, thresholds), Conditions on/off, "Model, modules and road settings", the save card.

### Added

- `SHOW_PROFILE_SELECT`, `SHOW_PROFILE_DESCRIPTION`, `SHOW_PROFILE_HISTORY` (all `false`)

### Deleted

None.

---

# Configuration page: save card hidden, tab renamed "Thresholds"

File: `frontend/src/pages/Config.tsx`

## 2026-10-15

Nothing was deleted.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **Save card** ("No changes." / "N change(s)", the "Why this change?" note box, **Save new version**, **Reset**, and the schema-error alert inside it) | Rendered only when `SHOW_SAVE_CARD` is true (constant above `ProfileEditor`) |

**Effect:** thresholds and switches can still be changed on screen, but nothing can be saved from the page while the card is hidden; a page reload shows the stored values again. Set `SHOW_SAVE_CARD = true` to save changes.

### Changed

| Item | Before | After |
|---|---|---|
| First tab label | Profiles & thresholds | Thresholds (tab value `profiles` unchanged) |

### Added

- `const SHOW_SAVE_CARD = false;`

### Deleted

None.

---

# Configuration > Thresholds: only the four types in use

File: `frontend/src/pages/Config.tsx`

## 2026-10-15

Nothing was deleted.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| Threshold cards of **wrong way, illegal U-turn, lane violation, red light** (and any other type not listed) | Only types in `SHOWN_TYPES` get a card: `speeding`, `no_parking`, `highway_stop`, `zebra_crossing` |

The hidden types keep their settings in the profile (and in the engine): this only filters the list on the page. To show them again, add them to `SHOWN_TYPES`, or set it to `null` for all.

### Still shown

"Conditions on/off" and "Model, modules and road settings" (unchanged, still list everything).

### Added

- `const SHOWN_TYPES: ViolationType[] | null = ['speeding', 'no_parking', 'highway_stop', 'zebra_crossing'];`

### Deleted

None.

---

# Configuration > Thresholds: "Conditions on/off" and "Model, modules and road settings" hidden

File: `frontend/src/pages/Config.tsx`

## 2026-10-15

Nothing was deleted.

| Section | How it is hidden |
|---|---|
| **Conditions on/off** (one switch per condition id) | Rendered only when `SHOW_CONDITIONS` is true |
| **Model, modules and road settings** (JSON boxes: model, modules, road, place_memory, applies_to) | Rendered only when `SHOW_MODEL_SETTINGS` is true |

To bring them back, set the constant to `true` (above `ProfileEditor`). The values stay in the profile and the engine keeps using them.

Still shown: the four threshold cards (speeding, no parking, highway stopping, zebra crossing).

Added: `const SHOW_CONDITIONS = false;`, `const SHOW_MODEL_SETTINGS = false;`. Deleted: none.

---

# Configuration > Zones: No U-turn hidden, opens on Town04

File: `frontend/src/components/ZoneDrawer.tsx`

## 2026-10-15

Nothing was deleted.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **No U-turn** in the zone-type picker next to Draw zone, in the colour legend, and in the New zone / Zone settings form's Type list | `HIDDEN_ZONE_TYPES` (`no_u_turn`) filtered out of `shownTypeOptions` |

Existing No U-turn zones (if any) still show on the map and in the list, and their settings form still lists their own type. The type is still in `lib/zones.ts`, the backend and the engine; the 3D twin's own zone list is unchanged. To bring it back, empty `HIDDEN_ZONE_TYPES`.

### Changed

| Item | Before | After |
|---|---|---|
| Scene the tab opens on | Town05 | **Town04** (`DEFAULT_SCENE`), else the first town |

### Added

- `const DEFAULT_SCENE = 'Town04';`, `const HIDDEN_ZONE_TYPES`, `const shownTypeOptions`

### Deleted

None.

---

# 3D digital twin: "Custom traffic" switch hidden

File: `frontend/src/twin/TwinView.ts`

## 2026-10-15

Nothing was deleted.

| Section | How it is hidden |
|---|---|
| **Custom traffic** switch in the twin panel (the toggle, its "(off)" / vehicle-count note and the gear button that opens the traffic settings) | The `#customSw` label gets `style="display:none"` while `SHOW_CUSTOM_TRAFFIC` is false (an inline style, because `.tw-switch` CSS would override a plain `hidden` attribute) |

To bring it back, set `SHOW_CUSTOM_TRAFFIC = false` to `true` (constant near the top of `TwinView.ts`, next to `SHOW_LANE_EDIT`). The custom-traffic code is untouched: the scenario builder (right-click "Add Custom Rules") can still turn it on through its `customTraffic` hook.

Still shown: "Traffic from video" and the rest of the panel.

Added: `const SHOW_CUSTOM_TRAFFIC = false;`. Deleted: none.

---

# 3D digital twin: opens on Town04

File: `frontend/src/twin/TwinView.ts`

## 2026-10-15

| Item | Before | After |
|---|---|---|
| Town when neither `?town=` nor the session names one (live sessions carry none; also with no session) | The first town in the list | **Town04** (`DEFAULT_TOWN`), else the first town. The town picker is set to it before the session loads |

Recorded sessions that carry their own town still open on it; `?town=` still overrides.

Added: `const DEFAULT_TOWN = "Town04";`. Deleted: none.

---

# 3D digital twin: "Pins" dropdown hidden

File: `frontend/src/twin/TwinView.ts`

## 2026-10-15

Nothing was deleted.

| Section | How it is hidden |
|---|---|
| **Pins** dropdown (violations / violations & road surface / road surface / none) in the twin panel | Its label gets `style="display:none"` while `SHOW_PIN_SELECT` is false |

The pins on the map are unchanged: they keep the dropdown's default, **violations**. To bring the dropdown back, set `SHOW_PIN_SELECT = false` to `true` (constant near the top of `TwinView.ts`).

Added: `const SHOW_PIN_SELECT = false;`. Deleted: none.

---

# Violations page filters: "All" instead of an empty box

File: `frontend/src/components/Filters.tsx` (the shared filter bar: Violations, and the hidden Statistics page)

## 2026-10-15

| Item | Before | After |
|---|---|---|
| **Session, Type, Condition, Status, Review** dropdowns when not filtering | Empty box with a clear (x) button | **All** selected (a real first option, value `__all__`, sent to the API as "no filter"); picking All clears that filter. The x buttons are gone (there is always a value) |

From / To date boxes and Clear / Export unchanged. URLs and API queries are the same as before (All = the parameter is left out).

Added: `const ALL`, `withAll()`. Deleted: none (the clear buttons were the `clearable` prop, now off).

---

# Sessions page hidden

File: `frontend/src/App.tsx` (and one message in `pages/Violations.tsx`)

## 2026-10-15

Nothing was deleted. `pages/Sessions.tsx` and its import are untouched.

| Section | How it is hidden |
|---|---|
| **"Sessions" side-menu entry** (`NAV` item `/sessions`) | Added to `NAV` only when `SHOW_SESSIONS` is true |
| **`/sessions` route** (`<SessionsPage />`) | Rendered only when `SHOW_SESSIONS` is true; while hidden, `/sessions` redirects to `/live` |

**Effect:** importing a processed flight from the dashboard (the Sessions page's import) is not reachable while hidden; live sessions still appear by themselves (Live page, Violations Session filter). To bring it back, set `SHOW_SESSIONS = false` to `true` (constant above `NAV`).

### Changed

| Item | Before | After |
|---|---|---|
| Violations table, no matching events | No events match. Import a processed flight under Sessions. | No events match. |

Added: `const SHOW_SESSIONS = false;`. Deleted: none.

---

# Configuration > Thresholds: Profile selector and save card shown again

File: `frontend/src/pages/Config.tsx`

## 2026-10-15

| Item | Before | After |
|---|---|---|
| **Profile** selector | hidden (`SHOW_PROFILE_SELECT = false`) | shown (`true`) |
| **Save card** (change summary, note, Save new version, Reset) | hidden (`SHOW_SAVE_CARD = false`) | shown (`true`): threshold changes can be saved again |

Still hidden: the profile description line, the History card, "Conditions on/off", "Model, modules and road settings", and the threshold cards of types not in `SHOWN_TYPES`.

---

# Configuration > Thresholds: one setting per card (max speed, max stop time)

File: `frontend/src/pages/Config.tsx` (engine: `ml/violation_engine/rules.py`, `configs/profiles/default.json`; live: `services/live/pipeline.py`)

## 2026-10-15

Nothing was deleted.

### Shown per card (`SHOWN_PARAMS`)

| Card | Only setting shown | Engine param | Default |
|---|---|---|---|
| Speeding | **Max speed (km/h), 0 = the map's lane limits** | `speeding.max_speed_kmh` (**new**) | 0 (off) |
| No parking | **Max stop time (s)** | `no_parking.min_s` | 30 |
| Zebra crossing | **Max stop time on the crossing (s)** | `zebra_crossing.min_s` | 10 |
| Highway stopping | **Max stop time (s)** | `highway_stop.min_s` | 20 |

### Hidden (kept in the code and the profile)

All the other params of these four cards (speeding: min s, sigmas, tolerance, confirm / close gap, class limits, min track age; stop rules: max km/h, close gap). They keep their values in the profile. To show them again, remove the type from `SHOWN_PARAMS` (it then shows all its params with the generic labels).

### New engine setting

`max_speed_kmh` (speeding): when above 0, one limit for every road, used instead of the lanes' own limits; a drawn Speed-limit zone still wins over it, and a lower per-class limit still caps it. Events carry `limit_source: "max_speed"`. Tests: 3 new in `test_rules.Speeding` (replaces a lower lane limit; above the lane limit allows more, 0 = lane limits; a speed zone beats it).

### Live pipeline reads the saved profile

`pipeline.py --profile` now defaults to `default` (`configs/profiles/default.json`, the file the page writes on Save); before, it ran with `rules.DEFAULTS` only, so changes saved here did not reach the live run. `--profile none` = the old behaviour. The profile is read when the pipeline starts: restart it after saving.

### Added

- `SHOWN_PARAMS`, an optional `label` prop on `ParamInput`

### Deleted

None.

## 3D Digital Twin — plan builder bottom bar (2026-10-15)

### Hidden (kept in the code)

| Control | What it is | How it is hidden |
|---|---|---|
| **Custom traffic** switch | Virtual traffic on the map (blue), following the plan | Not rendered when `SHOW_VIRTUAL_TRAFFIC` is false (`frontend/src/twin/builder/PlanBuilder.ts`, `renderBottom`) |
| **Simulate virtual traffic** button | Baseline-vs-plan run of the virtual traffic model (several seeds), played on the map | Same constant |

To bring them back, set `SHOW_VIRTUAL_TRAFFIC = true` (a constant near the top of `PlanBuilder.ts`). Their handlers (`custom`, `simulate`) are unchanged; with the controls not drawn they never fire. "Analyse on recorded traffic", "Run in CARLA" and "Report" stay.

### Deleted

None.

---

# Recommendations page: plain English, Decide button hidden

Files: `frontend/src/pages/Recommendations.tsx`; generated text in `ml/planning/recommend.py` and `ml/planning/catalogue.py`

## 2026-10-15

Nothing was deleted. The recommendation data (fields, ids, scores, rules) is unchanged; only the words are.

### Hidden (kept in the code)

| Section | How it is hidden |
|---|---|
| **Decide…** button on each card (and so the decision dialog) | Rendered only when `SHOW_DECIDE` is true |
| The recommendation **id** (e.g. rec-174ccff9fb41) at the top right of each card | Rendered only when `SHOW_REC_ID` is true |
| The **area** badge (e.g. ILLEGAL STOPPING) and the **status** badge ("proposed") | Removed from the header; a decision badge still shows if one was made |

### Changed: page wording

| Before | After |
|---|---|
| HIGHWAY STOP · PRIORITY HIGH · 66.60 · MAJOR HOTSPOT | Highway stopping · Priority: High (the score is in the tooltip) · Major hotspot |
| → No-stopping enforcement… | **What to do:** … |
| Where: road 47 · 28.60 m around (-9.96, -27.55) · open in twin | Where: Town04, road 47, within about 29 m of one spot · show on the 3D map |
| Evidence: 123 events over 170.76 min · 70 % of the events in the area · mean confidence 0.95; badge "B1 123" | What we saw: 123 times in 171 minutes, 70% of all problems in this area. The system was 95% sure on average; badge "Stopping on the highway: 123" (condition names, not codes) |
| Expected impact: PROJECTED ESTIMATE …; now: 43.22 per hour (rate extrapolated from 10246 s of observation); source: … | How much it could help: "About X% to Y% fewer (…)" or "We do not know yet how much this would help; a simulator test can measure it"; "Right now: about 43 times an hour (based on 171 minutes of video)"; the source line only when a published figure is used |
| Confidence: HIGH · 0.92 mean event confidence … tier … | How sure we are: High (92%) — the system was 95% sure on average, a major hotspot, 11% still need a person to check them, seen in 5 recordings |
| CARLA validation: SIMULABLE: YES | Can we test it in the simulator? Yes / Partly / Only the rules / No (+ the reason in plain words) |
| How to validate / Limitations / Alternatives | How to test it / Things to keep in mind / Other options |
| Page intro, empty and count lines | "Suggestions based on where and how often rules are broken…", "No suggestions yet…", "N suggestions, most important first." |

### Changed: generated text (`recommend.py`, `catalogue.py`)

Every rule's **problem** and **action** sentence, the **limitations**, the **test plan** (method, success rule, what we measure), the **trade-offs** of the other options, the **confidence reasons**, the **priority explanation** (tooltip), the "cannot be simulated" reasons, and the countermeasure **names** were rewritten without jargon (no VMS, carriageway, TM, OpenDRIVE, PRD, seeds, TTC, …). Examples: "123 cars stopped on the highway (road 47 (highway))" / "Ban stopping here and enforce it, with a warning on an electronic sign; if cars keep stopping, build an emergency stopping place". The machine-readable parts (`primary_metric`, `simulation.change.carla`, scenario numbers) are unchanged. The backend imports `recommend.py` once: **restart the backend** to see the new generated text. Planning tests: 62 passed.

### Added

- `SHOW_DECIDE`, `SHOW_REC_ID`, `BAND_PLAIN`, `TIER_PLAIN`, `SIM_PLAIN`, `pct()` in the page; `_cars()`, `TIER_PLAIN`, `PLAIN_METRIC` in `recommend.py`

### Deleted

None.

---

# 3D twin plan builder: Scenarios, Traffic, Templates tabs and "Priority, schedule, note" hidden

File: `frontend/src/twin/builder/PlanBuilder.ts`

## 2026-10-15

Nothing was deleted.

| Section | How it is hidden |
|---|---|
| **Scenarios**, **Traffic** and **Templates** tabs | Only the tabs in `SHOWN_TABS` (`prop`, `rule`) are drawn. The tab code is untouched |
| **"Priority, schedule, note"** section of the Props / Rules form (Priority, Note, Active from, Active to) | The `<details>` gets `style="display:none"` while `SHOW_ITEM_EXTRAS` is false; its inputs stay in the form, so new items get the defaults (priority 0, always active, no note) and edited items keep their values |

To bring them back: `SHOWN_TABS = ['prop', 'rule', 'scenario', 'traffic', 'template']` and `SHOW_ITEM_EXTRAS = true` (constants under `JUNCTION_KINDS`). A saved plan that already has scenario items still shows and edits them (Edit opens their form). Vitest: 63/64, the 1 failure is the known Traffic-tab test from before.

Added: `SHOWN_TABS`, `SHOW_ITEM_EXTRAS`. Deleted: none.

---

# Recommendations page: short cards (count, place, solution)

File: `frontend/src/pages/Recommendations.tsx`

## 2026-10-15

Nothing was deleted.

| Item | Before | After |
|---|---|---|
| Card body | Problem sentence, "What to do", then Where / What we saw / How much it could help / How sure we are / Can we test it… and the How to test it / Things to keep in mind / Other options sections | Three lines: **"161 speeding violations recorded on road 47 (Town04)"** (built on the page from the evidence count, type and roads, with "show on the 3D map"), **"Solution: …"** (the action), and "For more detail, test this change in the CARLA simulator." |
| Header badges | type, priority, tier | type, priority (tier hidden) |

Everything else is behind `SHOW_DETAILS` (constant under `SHOW_REC_ID`); `true` brings back the full card exactly as before.

Added: `const SHOW_DETAILS = false;`. Deleted: none.

---

# Recommendations page: wrong-way and lane-violation cards hidden

File: `frontend/src/pages/Recommendations.tsx`

## 2026-10-16

Nothing was deleted.

| Section | How it is hidden |
|---|---|
| Cards of type **wrong way** and **lane violation** | Filtered out of the list (`HIDDEN_REC_TYPES`); the count line ("N suggestions") counts only the shown ones |

The backend still makes these recommendations (and stores them); to show them again, remove the type from `HIDDEN_REC_TYPES`.

### Changed

| Item | Before | After |
|---|---|---|
| Count line of a card | "7 lane violation violations recorded…" | "7 lane violations recorded…" (`countNoun()`: no doubled word, singular for 1) |

Added: `HIDDEN_REC_TYPES`, `countNoun()`. Deleted: none.
