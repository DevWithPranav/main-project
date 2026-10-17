"""Zones drawn in the dashboard applied to the running live engine (zone_watch.py + SceneMap.replace_zones):
a car stopped where no zone was is flagged once the zone file gains a no-parking zone there, and a car
stopped there after the zone is deleted is not.

Run:  venv/Scripts/python.exe -m pytest services/live/tests/test_zone_watch.py -q
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "live"))
sys.path.insert(0, str(REPO / "ml" / "violation_engine"))
sys.path.insert(0, str(REPO / "ml" / "violation_engine" / "tests"))

from online import IncrementalEngine  # noqa: E402
from rules import Engine  # noqa: E402
from test_rules import drive_stop_leave, rect, scene, track  # noqa: E402
from zone_watch import ZoneWatcher  # noqa: E402

ZONE = {"id": "z_np", "type": "no_parking", "polygon": rect(50, 70, -1.75, 1.75), "grace_s": 10.0, "source": "api"}


def write(path: Path, zones: list[dict]) -> None:
    path.write_text(json.dumps({"scene": "test", "zones": zones}))


def frames(rows):
    by = defaultdict(list)
    for r in rows:
        by[(r["time_s"], r["frame"])].append(r)
    return sorted(by.items())


def stopped_in_zone(ie, rows, before=None):
    """Step the engine through rows (calling before(t) first on each frame); the no_parking events opened."""
    out = []
    for (t, fr), rs in frames(rows):
        if before:
            before(t)
        opened, _ = ie.step(t, fr, rs)
        out += [e for e in opened if e.type == "no_parking"]
    return out


def test_a_zone_saved_mid_run_applies_and_a_deleted_one_stops(tmp_path):
    path = tmp_path / "test.json"
    sc = scene()
    zw = ZoneWatcher(path, sc, every=0.0)
    assert zw.poll(force=True) is None and sc.zones == []  # no file yet: nothing to load

    ie = IncrementalEngine(Engine(sc))
    first = track(drive_stop_leave(60, 0, 30), tid=1)
    assert stopped_in_zone(ie, first) == []  # no zone yet

    write(path, [ZONE])  # a planner saves the zone in the dashboard
    assert zw.poll() == 1 and [z.id for z in sc.zones] == ["z_np"]
    t0 = first[-1]["time_s"] + 5
    second = track(drive_stop_leave(60, 0, 30), tid=2, t0=t0)
    ev = stopped_in_zone(ie, second)
    assert len(ev) == 1 and ev[0].zone_id == "z_np"

    write(path, [])  # and deletes it
    assert zw.poll() == 0 and sc.zones == []
    third = track(drive_stop_leave(60, 0, 30), tid=3, t0=second[-1]["time_s"] + 5)
    assert stopped_in_zone(ie, third) == []
    assert zw.reloads == 2


def test_same_ids_replace_not_double_and_other_zones_stay(tmp_path):
    path = tmp_path / "test.json"
    write(path, [ZONE])
    other = {"id": "staged", "type": "no_u_turn", "polygon": rect(0, 10, -2, 2)}  # e.g. from a scenario log
    sc = scene(zones=[ZONE, other])  # the same file was also given as --zones
    zw = ZoneWatcher(path, sc, every=0.0)
    assert zw.poll(force=True) == 1
    assert sorted(z.id for z in sc.zones) == ["staged", "z_np"]
    moved = {**ZONE, "polygon": rect(100, 120, -1.75, 1.75)}
    write(path, [moved])
    assert zw.poll() == 1
    assert sorted(z.id for z in sc.zones) == ["staged", "z_np"]
    assert sc.zones_at(110, 0, {"no_parking"}) and not sc.zones_at(60, 0, {"no_parking"})
    assert zw.poll() is None  # unchanged file: nothing to do


def test_a_half_written_file_is_retried_and_the_old_zones_kept(tmp_path):
    path = tmp_path / "test.json"
    write(path, [ZONE])
    sc = scene()
    zw = ZoneWatcher(path, sc, every=0.0)
    zw.poll(force=True)
    path.write_text('{"zones": [{"id": "z_np", "ty')  # caught mid-write
    assert zw.poll() is None and [z.id for z in sc.zones] == ["z_np"]
    write(path, [])
    assert zw.poll() == 0 and sc.zones == []
