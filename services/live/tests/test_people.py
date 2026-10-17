"""Pedestrians in the live pipeline (people.py): the crosswalk gate runs the people detector only near a
crossing (and HOLD_S after), people rows come out with offset ids and the pedestrian class, and
merge_batches gives the engine one batch per frame.

Run:  venv/Scripts/python.exe -m pytest services/live/tests/test_people.py -q
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "live"))
sys.path.insert(0, str(REPO / "ml" / "violation_engine"))

from lane_map import _zone  # noqa: E402
from online import LiveCamera  # noqa: E402
from people import HOLD_S, PEOPLE_ID_OFFSET, CrosswalkGate, PeoplePath, merge_batches  # noqa: E402

CROSSWALK = {"id": "cw", "type": "crosswalk", "polygon": [[-4, -2], [4, -2], [4, 2], [-4, 2]]}


class FakeTracker:
    """Stands in for pedestrians.PeopleTracker: one walker in the middle of the frame, moving 4 px a call."""

    def __init__(self):
        self.calls = 0

    def update(self, img):
        self.calls += 1
        return [{"tracker_id": 7, "cls": "pedestrian", "raw_cls": "pedestrian", "conf": 0.6,
                 "cx": 960.0 + 4 * self.calls, "cy": 540.0, "w": 12.0, "h": 12.0}]


def nadir(cam: LiveCamera, frame: int, x: float, y: float, z: float = 60.0) -> None:
    cam.set_pose(frame, (x, y, z, -90.0, 0.0, 0.0))


def test_gate_near_and_hold():
    cam = LiveCamera(1920, 1080, 90.0, 0.0)
    gate = CrosswalkGate([_zone(CROSSWALK)])
    nadir(cam, 1, 0.0, 0.0)
    assert gate(cam, 1, 0.0)  # crossing under the camera
    nadir(cam, 2, 1000.0, 0.0)
    assert gate(cam, 2, HOLD_S - 0.1)  # gone from the view, still held
    nadir(cam, 3, 1000.0, 0.0)
    assert not gate(cam, 3, HOLD_S + 0.1)
    # a crossing 10 m outside the footprint is within the margin; 20 m outside is not
    nadir(cam, 4, 0.0, 0.0)
    fp = gate.footprint(cam, 4)
    half_x = (fp.bounds[2] - fp.bounds[0]) / 2
    for out_m, near in ((10.0, True), (20.0, False)):
        nadir(cam, 5, 4.0 + half_x + out_m, 0.0)
        assert gate.near(gate.footprint(cam, 5)) is near


def test_no_crosswalk_scene_is_not_available():
    path = PeoplePath([_zone({"id": "np", "type": "no_parking", "polygon": CROSSWALK["polygon"]})], 1.0, "fixedlag",
                      True, tracker=FakeTracker())
    assert not path.available


def test_detector_runs_only_near_a_crossing_and_rows_are_offset():
    cam = LiveCamera(1920, 1080, 90.0, 0.0)
    trk = FakeTracker()
    path = PeoplePath([_zone(CROSSWALK)], 0.5, "fixedlag", True, tracker=trk)
    rows, states = [], []
    for k in range(40):  # 4 s at 10 fps over the crossing
        nadir(cam, k, 0.0, 0.0)
        dets, live = path.step(k, k * 0.1, None, cam)
        states += live
        for _, _, rs in path.emit(k * 0.1):
            rows += rs
    calls_on = trk.calls
    assert calls_on == 40 and path.frames_on == 40
    for k in range(40, 80):  # flown 1 km away: after the hold, no more detector calls
        nadir(cam, k, 1000.0, 0.0)
        path.step(k, k * 0.1, None, cam)
    assert trk.calls == calls_on + round(HOLD_S / 0.1)
    rows += [r for _, _, rs in path.flush() for r in rs]
    assert rows, "the walker never reached the engine"
    assert all(r["track_id"] >= PEOPLE_ID_OFFSET and r["class"] == "pedestrian" for r in rows)
    assert all(s["track_id"] >= PEOPLE_ID_OFFSET and s["cls"] == "pedestrian" for s in states)
    assert dets and dets[0]["track_id"] >= PEOPLE_ID_OFFSET and len(dets[0]["xywh"]) == 4


def test_merge_batches_one_per_frame_in_time_order():
    a = [(0.1, 1, [{"track_id": 1}]), (0.2, 2, [{"track_id": 1}])]
    b = [(0.0, 0, [{"track_id": PEOPLE_ID_OFFSET + 1}]), (0.2, 2, [{"track_id": PEOPLE_ID_OFFSET + 1}])]
    m = merge_batches(a, b)
    assert [fr for _, fr, _ in m] == [0, 1, 2]
    assert [r["track_id"] for r in m[2][2]] == [1, PEOPLE_ID_OFFSET + 1]
