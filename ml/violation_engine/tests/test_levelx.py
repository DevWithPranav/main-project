"""Tests for the highD / inD loaders (levelx.py, B3) on synthetic recordings in the datasets' formats.

Run:  python -m unittest discover -s ml/violation_engine/tests -v
"""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import levelx  # noqa: E402

FPS = 25
LEN, WID = 4.5, 1.8


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def highd_track(tid, x0, y, vx, dur, lane_change_to=None, t_change=None):
    """Box rows (top-left x, y) of one vehicle; optional lane change over 3 s from t_change."""
    rows = []
    for k in range(int(dur * FPS)):
        t = k / FPS
        yc = y
        if lane_change_to is not None and t >= t_change:
            a = min(1.0, (t - t_change) / 3.0)
            yc = y + (lane_change_to - y) * (3 * a * a - 2 * a ** 3)
        xc = x0 + vx * t
        rows.append({"frame": k + 1, "id": tid, "x": round(xc - LEN / 2, 3), "y": round(yc - WID / 2, 3),
                     "width": LEN, "height": WID, "xVelocity": vx, "yVelocity": 0.0, "laneId": 0})
    return rows


class HighD(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        # upper carriageway y 8.5-16.5 (towards -x), lower y 21.5-29.5 (towards +x); 120 km/h limit
        write_csv(root / "01_recordingMeta.csv", [{"id": 1, "frameRate": FPS, "locationId": 1, "speedLimit": 33.33,
                                                  "duration": 12, "upperLaneMarkings": "8.5;12.5;16.5",
                                                  "lowerLaneMarkings": "21.5;25.5;29.5"}])
        tracks = (highd_track(1, 10, 23.5, 40.0, 10)            # 144 km/h in 120: speeding
                  + highd_track(2, 10, 27.5, 30.0, 10)          # 108 km/h: fine
                  + highd_track(3, 10, 23.5, 30.0, 10, 27.5, 3)  # lane change across the dashed line
                  + highd_track(4, 400, 10.5, -30.0, 10)        # upper carriageway, right way
                  + highd_track(5, 10, 14.5, 25.0, 10))         # upper carriageway driven towards +x: wrong way
        write_csv(root / "01_tracks.csv", tracks)
        write_csv(root / "01_tracksMeta.csv", [{"id": i, "class": "Truck" if i == 5 else "Car",
                                               "drivingDirection": 1 if i in (4, 5) else 2} for i in range(1, 6)])
        cls.summary = levelx.run_recording("highd", root, "01", None, 10.0, root / "out")
        cls.events = json.loads((root / "out" / "highd_01" / "violations.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def counted(self, vtype):
        return [e for e in self.events if e["type"] == vtype and e["status"] in levelx.COUNTED_STATUS]

    def test_everything_on_a_lane(self):
        self.assertGreater(self.summary["rows_on_a_lane"], 0.98)

    def test_speeding_matches_dataset_speeds(self):
        s = self.summary["speeding_vs_dataset_speeds"]
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (1, 0, 0))
        self.assertEqual({t for e in self.counted("speeding") for t in e["track_ids"]}, {1})

    def test_dashed_lane_change_is_legal(self):
        self.assertEqual(self.summary["solid_line_crossings"], 0)
        self.assertEqual(self.counted("lane_violation"), [])

    def test_wrong_way_only_the_truck(self):
        self.assertEqual({t for e in self.counted("wrong_way") for t in e["track_ids"]}, {5})


class Lanelet(unittest.TestCase):
    """Two opposite lanelets along local x (inD style: lat/lon nodes, local = UTM - origin)."""

    @classmethod
    def setUpClass(cls):
        import utm
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        e0, n0, zone, letter = utm.from_latlon(50.78, 6.08)
        node_id = iter(range(1, 10000))
        nodes, ways = [], []

        def way(pts, wid, ltype, sub):
            refs = []
            for x, y in pts:
                lat, lon = utm.to_latlon(e0 + x, n0 + y, zone, letter)
                i = next(node_id)
                nodes.append(f'<node id="{i}" lat="{lat:.9f}" lon="{lon:.9f}"/>')
                refs.append(i)
            ways.append(f'<way id="{wid}">' + "".join(f'<nd ref="{r}"/>' for r in refs)
                        + f'<tag k="type" v="{ltype}"/><tag k="subtype" v="{sub}"/></way>')

        xs = np.linspace(0, 100, 11)
        way([(x, 3.5) for x in xs], 101, "line_thin", "solid_solid")  # A's left (towards +x, left is +y)
        way([(x, 0.0) for x in xs], 102, "curbstone", "")
        way([(x, 3.5) for x in xs[::-1]], 103, "line_thin", "solid_solid")  # B's left (towards -x, left is -y)
        way([(x, 7.0) for x in xs[::-1]], 104, "curbstone", "")
        rels = "".join(f'<relation id="{rid}"><member type="way" ref="{l}" role="left"/>'
                       f'<member type="way" ref="{r}" role="right"/><tag k="type" v="lanelet"/>'
                       f'<tag k="subtype" v="road"/></relation>' for rid, l, r in ((201, 101, 102), (202, 103, 104)))
        (root / "map.osm").write_text(f'<osm>{"".join(nodes)}{"".join(ways)}{rels}</osm>')
        write_csv(root / "07_recordingMeta.csv", [{"recordingId": 7, "frameRate": FPS, "speedLimit": 13.8889, "duration": 10,
                                                  "latLocation": 50.78, "lonLocation": 6.08,
                                                  "xUtmOrigin": e0, "yUtmOrigin": n0}])
        rows = []
        for tid, (x0, y, vx) in {1: (5, 1.75, 10.0), 2: (95, 1.75, -10.0)}.items():  # 2 drives lane A backwards
            for k in range(8 * FPS):
                rows.append({"recordingId": 7, "trackId": tid, "frame": k, "xCenter": x0 + vx * k / FPS, "yCenter": y})
        write_csv(root / "07_tracks.csv", rows)
        write_csv(root / "07_tracksMeta.csv", [{"recordingId": 7, "trackId": 1, "class": "car"},
                                              {"recordingId": 7, "trackId": 2, "class": "car"}])
        cls.scene = levelx.lanelet_scene(root / "map.osm", (e0, n0), 50.0)
        cls.summary = levelx.run_recording("ind", root, "07", root / "map.osm", 10.0, root / "out")
        cls.events = json.loads((root / "out" / "ind_07" / "violations.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_lanes_parsed(self):
        lanes = {l["id"]: l for l in self.scene["lanes"]}
        self.assertEqual(set(lanes), {"ll201", "ll202"})
        a = np.array(lanes["ll201"]["centreline"])
        self.assertAlmostEqual(float(np.median(a[:, 1])), 1.75, delta=0.05)  # UTM round trip
        self.assertGreater(a[-1, 0], a[0, 0])  # drives towards +x
        self.assertAlmostEqual(lanes["ll201"]["width_m"], 3.5, delta=0.05)
        self.assertEqual(lanes["ll201"]["left_line"], "solidsolid")
        self.assertFalse(lanes["ll201"]["junction"])

    def test_wrong_way_car_flagged(self):
        ww = [e for e in self.events if e["type"] == "wrong_way" and e["status"] in levelx.COUNTED_STATUS]
        self.assertEqual({t for e in ww for t in e["track_ids"]}, {2})


if __name__ == "__main__":
    unittest.main()
