"""Zones drawn in the dashboard, applied to the running live engine (services/live/pipeline.py).

The backend writes the active zones of a map to <ZONE_OUT_DIR>/<map>.json whenever a planner saves, edits or
deletes one (backend/app/routers/zones.py; default backend/data/zones). The pipeline loads that file when it
starts and then checks it every EVERY_S seconds: when it changed, the zones it last took from the file are
replaced by the file's current ones (SceneMap.replace_zones), so a new zone applies from the next frame and
a deleted one stops raising events. Zones from a --zones scenario log or the lane map itself are left alone
(they have other ids). A zone with the same id as one already in the scene (the same file passed as --zones)
replaces it rather than doubling it.

Pedestrian crosswalk rules (zebra_pedestrians.py) take their crosswalk zones once at start; vehicle rules
(no parking, no stopping, highway stop, speed zones, no U-turn) all read the current zones.
"""

import json
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ZONE_DIR = Path(os.getenv("ZONE_OUT_DIR", REPO / "backend" / "data" / "zones"))  # as backend/app/config.py
EVERY_S = 2.0


class ZoneWatcher:
    def __init__(self, path: Path, scene, every: float = EVERY_S):
        self.path, self.scene, self.every = Path(path), scene, every
        self.ids: set[str] = set()   # zones taken from the file, replaced on the next change
        self.stamp = None            # (mtime_ns, size) of the version applied
        self.next_check = 0.0
        self.reloads = 0             # changes applied after the first check (the start)
        self._checked = False

    def poll(self, force: bool = False) -> int | None:
        """Apply the file if it changed since the last call (at most every `every` s unless force).
        Returns the number of zones now taken from it, or None when nothing changed."""
        now = time.monotonic()
        if not force and now < self.next_check:
            return None
        first, self._checked = not self._checked, True
        self.next_check = now + self.every
        try:
            st = self.path.stat()
        except FileNotFoundError:
            st = None
        stamp = (st.st_mtime_ns, st.st_size) if st else None
        if stamp == self.stamp:
            return None
        zones = []
        if st is not None:
            try:
                zones = json.loads(self.path.read_text()).get("zones", [])
            except (OSError, ValueError):
                return None  # caught mid-write: the next check reads it whole
        try:
            self.scene.replace_zones(self.ids, zones)
        except Exception as e:  # noqa: BLE001 - a bad zone must not stop the pipeline; the old ones stay
            print(f"[zones] {self.path.name} not applied ({e}); keeping the previous zones")
            self.stamp = stamp
            return None
        self.ids, self.stamp = {str(z["id"]) for z in zones}, stamp
        if not first:
            self.reloads += 1
        kinds = ", ".join(sorted({str(z["type"]) for z in zones})) or "none"
        print(f"[zones] {'loaded' if first else 'reloaded'} {len(zones)} zone(s) from {self.path} ({kinds})", flush=True)
        return len(zones)
