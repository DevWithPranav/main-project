# frontend/

Web UIs (Build Plan **M6** and **M7**). Not built yet.

- **Configuration Dashboard** (M6, React / Next.js): violation types and conditions on/off with thresholds, model and tracker settings, modules, road configuration, profiles; live monitoring; violation review; statistics and exports.
- **3D Digital Twin** (M7, CesiumJS): roads built from the OpenDRIVE lane map in map metres at a configurable anchor; live vehicles, violation pins, hotspots; planner edits limited to what CARLA can simulate (Expected Output Section 5.3).

Forms are driven by `schemas/profile.schema.json` and `schemas/conditions.json`, so the UI and the engine agree on every setting.
