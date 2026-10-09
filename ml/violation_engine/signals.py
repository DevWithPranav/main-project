"""Traffic-signal states for the red-light rule (Violation Engine, V4 - optional, CARLA only).

docs/Violation_Engine_Architecture.md, Section 5.6 (V4) and R22. Real footage can't see the
lights (they face the drivers), so the states come from the simulator:

traffic_lights.json, written by simulation/carla_scripts/record_flight.py:
    {"lights": [{"id": "<OpenDRIVE signal id>", "actor_id": 123,
                 "stop_lines": [{"id": "tl_<id>_<k>", "line": [[x, y], [x, y]], "signal_id": "<id>",
                                 "dir": [dx, dy]}]}],
     "changes": [[carla_frame, sim_time, "<id>", "Red"], ...]}   # every state change, first tick included

Stop lines come from CARLA's own TrafficLight.get_stop_waypoints() (one per lane the light
controls); "dir" is the approach direction (driving direction of the lane at the line).

States are looked up by frame (the flight's frame index, via frame_times.csv's carla_frame),
so there is no time base to reconcile: a change is in effect from the first frame recorded at
or after its tick.
"""

import bisect
import csv
import json
from collections import defaultdict
from pathlib import Path


class SignalLog:
    """signal id -> state changes on the trajectories' frame axis."""

    def __init__(self, changes: dict[str, list[tuple[int, str]]]):
        self.changes = {sid: sorted(ch) for sid, ch in changes.items()}
        self._frames = {sid: [f for f, _ in ch] for sid, ch in self.changes.items()}

    def state_at(self, signal_id, frame: int) -> tuple[str, int] | None:
        """(state, frame the state began) of a signal at a frame, or None if unknown."""
        sid = str(signal_id)
        fr = self._frames.get(sid)
        if not fr:
            return None
        k = bisect.bisect_right(fr, frame) - 1
        return None if k < 0 else (self.changes[sid][k][1], self.changes[sid][k][0])

    @classmethod
    def from_flight(cls, flight: Path) -> tuple["SignalLog", list[dict]] | None:
        """From a recorded flight's traffic_lights.json; None if the flight has none (older recorder)."""
        path = flight / "traffic_lights.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        with open(flight / "frame_times.csv", newline="") as f:
            pairs = sorted((int(r["carla_frame"]), int(r["frame"])) for r in csv.DictReader(f))
        cfs = [c for c, _ in pairs]
        changes = defaultdict(list)
        for cf, _, sid, state in data["changes"]:
            k = bisect.bisect_left(cfs, int(cf))  # first recorded frame at or after the change
            if k < len(pairs):
                changes[str(sid)].append((pairs[k][1], str(state)))
        lines = [sl for light in data["lights"] for sl in light["stop_lines"]]
        return cls(changes), lines

    @classmethod
    def from_plan(cls, plan: dict, act_t0: list[float], hz: float = 10.0) -> "SignalLog":
        """From a stage_violations.py --plan-only log: each red-light act's planned schedule
        (green at the act's start, red at red_at_s), on the dry run's frame axis (t * hz)."""
        changes = defaultdict(list)
        for act, t0 in zip(plan["acts"], act_t0):
            sig = act.get("signal")
            if not sig:
                continue
            changes[str(sig["id"])].append((int(round(t0 * hz)), "Green"))
            changes[str(sig["id"])].append((int(round((t0 + sig["red_at_s"]) * hz)), "Red"))
            changes[str(sig["id"])].append((int(round((t0 + act["duration_s"]) * hz)), "Green"))
        return cls(changes)
