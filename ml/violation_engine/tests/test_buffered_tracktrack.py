"""buffered_tracktrack.py: buffer 0 is stock TrackTrack; a buffer lets a car that moved ~its own length still match."""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import buffered_tracktrack as bt  # noqa: E402
from ultralytics.trackers import track as ul_track  # noqa: E402
from ultralytics.trackers.track_tracker import _hmiou_distance  # noqa: E402


def box(x0, y0, x1, y1):
    return SimpleNamespace(xyxy=np.array([x0, y0, x1, y1], dtype=np.float32))


CAR = box(100, 100, 163, 127)  # a 63 x 27 px car from 67 m
MOVED = [box(100 + d, 100, 163 + d, 127) for d in (20, 50, 70)]  # 1.5, 3.6, 5.1 m along the road


def test_buffer_zero_is_stock():
    iou0, cost0 = bt.hmiou_distance([CAR], MOVED, 0.0)
    iou_s, cost_s = _hmiou_distance([CAR], MOVED)
    assert np.allclose(iou0, iou_s) and np.allclose(cost0, cost_s)


def test_buffer_keeps_a_fast_car_matchable():
    _, stock = bt.hmiou_distance([CAR], MOVED, 0.0)
    _, buf = bt.hmiou_distance([CAR], MOVED, 1.0)
    match_thresh = 0.7  # tracktrack_live.yaml
    assert (stock[0] < match_thresh).tolist() == [True, False, False]  # stock: only the slow one
    assert (buf[0] < match_thresh).all()  # buffered: all three
    assert buf[0, 0] < buf[0, 1] < buf[0, 2]  # still prefers the nearer box


def test_registered_and_config_loads():
    assert ul_track.TRACKER_MAP["tracktrack"] is bt.BufferedTrackTrack
    cfg = Path(__file__).resolve().parents[1] / "tracktrack_live.yaml"
    from ultralytics.utils import YAML
    d = YAML.load(cfg)
    assert d["tracker_type"] == "tracktrack" and d["iou_buffer"] == 1.0 and d["min_track_len"] == 1


@pytest.mark.parametrize("buf", [0.0, 1.0])
def test_tracker_runs_on_moving_boxes(buf):
    """A car moving 60 px per frame (~120 km/h at 7 fps): with the buffer it keeps one id from frame 2 on."""
    from ultralytics.utils import YAML, IterableSimpleNamespace
    cfg = YAML.load(Path(__file__).resolve().parents[1] / "tracktrack_live.yaml")
    cfg.update(iou_buffer=buf, gmc_method="none")
    tr = bt.BufferedTrackTrack(IterableSimpleNamespace(**cfg))
    ids = []
    for k in range(6):
        x = 100 + 60 * k
        res = SimpleNamespace(conf=np.array([0.9]), cls=np.array([0.0]), xywh=np.array([[x + 31.5, 113.5, 63, 27]]),
                              xyxy=np.array([[x, 100, x + 63, 127]]))
        out = tr.update(res)
        ids.append(int(out[0, 4]) if len(out) else None)
    if buf:
        assert ids[0] is not None and len(set(ids[1:])) == 1 and None not in ids  # one track, boxed throughout
    else:
        assert len({i for i in ids if i is not None}) > 1 or None in ids[1:]  # stock: restarts or gaps
