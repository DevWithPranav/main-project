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
