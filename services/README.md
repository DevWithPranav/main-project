# services/

Live pipeline (Build Plan **M4**). Not built yet.

- **Streamer** (simulator side, `venv_sim`): sends frames + camera pose + simulator time over the network, next to `simulation/carla_scripts/record_flight.py`.
- **Detector service** (main `venv`): detection → tracking → ground coordinates → forward-only kinematics → violation engine (`ml/violation_engine/rules.py` `Engine.step`), frame by frame; publishes vehicle states and events to the backend.

The same code runs on one machine (localhost) or two (presentation). Targets: event-to-dashboard ≤ 3 s, twin at 10 Hz.
