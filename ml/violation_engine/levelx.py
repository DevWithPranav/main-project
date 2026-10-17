"""Violation engine on real drone trajectories: highD, inD, rounD (levelXdata) - Phase B step B3.

docs/Violation_Engine_Architecture.md, Section 6, level "L1 oracle (real)": the datasets ship
real vehicle trajectories (about 10 cm accurate) and the road layout, so Layers 3-7 run on real
driving without our detector or tracker. What it measures:
  - false alarms per hour of real traffic, per violation type
  - highD: the speeding rule against the dataset's own speeds and the recording's speed limit
    (a track truly speeds when its measured speed stays above limit + tolerance >= min_s)
  - highD: every inner lane line is dashed, so a solid-line crossing would be a false alarm

Formats (levelXdata documentation; the datasets need a free non-commercial access request):
  highD   <id>_tracks.csv (frame, id, x, y = box top-left, width, height, xVelocity, yVelocity,
          laneId ...), <id>_tracksMeta.csv (id, class, drivingDirection ...),
          <id>_recordingMeta.csv (frameRate, speedLimit m/s or -1, upperLaneMarkings,
          lowerLaneMarkings = "y;y;..." in m). Image axes: x right, y down (left-handed).
          drivingDirection 1 = upper lanes, towards -x; 2 = lower lanes, towards +x.
          The outermost markings of each carriageway are solid edges, the inner ones dashed.
  inD /   <id>_tracks.csv (trackId, frame, xCenter, yCenter, ...), <id>_tracksMeta.csv (trackId,
  rounD   class), <id>_recordingMeta.csv (frameRate, speedLimit m/s, latLocation, lonLocation,
          xUtmOrigin, yUtmOrigin). Local metres = UTM - origin (right-handed). The road layout
          is a Lanelet2 map (.osm, nodes in lat/lon): each lanelet's left/right bound ways give
          the lane, its direction and line types (line_thin/thick + solid/dashed, curbstone, virtual).
          Lanelets that overlap others (inside an intersection or roundabout) count as junction
          lanes, where the wrong-way and lane rules don't apply.

Usage:
    python ml/violation_engine/levelx.py highd <highD data dir> --recordings 01 02 03
    python ml/violation_engine/levelx.py ind <inD data dir> --recordings 07 --osm <maps>/location1.osm
    python ml/violation_engine/levelx.py rounD <rounD data dir> --recordings 00 --osm <maps>/location0.osm
"""

import argparse
import csv
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import shapely

from events import COUNTED_STATUS, write_events
from kinematics import smooth_track
from lane_map import SceneMap
from predicates import speed_tolerance_kmh
from rules import DEFAULTS, Engine

MEAS_SIGMA_M = 0.1  # levelXdata trajectories: ~10 cm position accuracy
LANE_STEP_M = 10.0  # highD centreline point spacing
JUNCTION_OVERLAP = 0.2  # a lanelet overlapping other lanelets by this share of its area is a junction lane
CLASSES = {"car": "car", "truck": "truck", "bus": "bus", "truck_bus": "truck", "van": "car"}
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "results" / "levelx"


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# --- tracks -> kinematics rows ------------------------------------------------------------------

def smooth_rows(tracks: dict[int, dict], hz: float) -> list[dict]:
    """tracks: id -> {"cls", "t", "x", "y"} (arrays at the dataset's frame rate) -> kinematics rows at
    about hz (every k-th frame), smoothed like our own tracks."""
    rows = []
    for tid, tr in tracks.items():
        t, x, y = (np.asarray(tr[k], float) for k in ("t", "x", "y"))
        if len(t) < 2:
            continue
        step = max(1, int(round((len(t) - 1) / max(t[-1] - t[0], 1e-9) / hz)))
        t, x, y = t[::step], x[::step], y[::step]
        if len(t) < 3:
            continue
        k = smooth_track(t, x, y, np.ones(len(t), bool), meas_sigma=MEAS_SIGMA_M)
        for i in range(len(t)):
            h = k["heading_deg"][i]
            rows.append({"frame": int(round(t[i] * hz)), "time_s": round(float(t[i]), 3), "track_id": tid,
                         "class": tr["cls"], "conf": 1.0, "visible": 1, "x": round(float(k["x"][i]), 3),
                         "y": round(float(k["y"][i]), 3), "vx": round(float(k["vx"][i]), 3), "vy": round(float(k["vy"][i]), 3),
                         "speed_kmh": round(float(k["speed_kmh"][i]), 2),
                         "speed_sigma_kmh": round(float(k["speed_sigma_kmh"][i]), 2),
                         "heading_deg": "" if math.isnan(h) else round(float(h), 1)})
    rows.sort(key=lambda r: (r["time_s"], r["track_id"]))
    return rows


# --- highD ----------------------------------------------------------------------------------------

def highd_recording(root: Path, rid: str) -> tuple[dict, dict, dict]:
    """-> (tracks, scene, meta). tracks also carry the dataset's own speed per frame ("v")."""
    meta = read_csv(root / f"{rid}_recordingMeta.csv")[0]
    tmeta = {int(r["id"]): r for r in read_csv(root / f"{rid}_tracksMeta.csv")}
    fps = float(meta["frameRate"])
    cols = defaultdict(lambda: defaultdict(list))
    x_max = 0.0
    for r in read_csv(root / f"{rid}_tracks.csv"):
        tid = int(r["id"])
        w, h = float(r["width"]), float(r["height"])
        c = cols[tid]
        c["t"].append(int(r["frame"]) / fps)
        c["x"].append(float(r["x"]) + w / 2)
        c["y"].append(float(r["y"]) + h / 2)
        c["v"].append(math.hypot(float(r["xVelocity"]), float(r["yVelocity"])) * 3.6)
        x_max = max(x_max, float(r["x"]) + w)
    tracks = {}
    for tid, c in cols.items():
        cls = CLASSES.get(str(tmeta[tid]["class"]).lower())
        if cls is not None:
            tracks[tid] = {"cls": cls, **{k: np.array(v) for k, v in c.items()}}
    limit = float(meta["speedLimit"])
    limit_kmh = None if limit <= 0 else round(limit * 3.6, 1)
    return tracks, highd_scene(meta, x_max + 20.0, limit_kmh), {"fps": fps, "speed_limit_kmh": limit_kmh,
                                                                  "duration_s": float(meta.get("duration", 0) or 0)}


def highd_scene(meta: dict, length_m: float, limit_kmh: float | None) -> dict:
    """Straight lanes between consecutive lane markings. Upper carriageway drives towards -x,
    lower towards +x; outermost markings solid (edges), inner dashed."""
    lanes = []
    xs = np.arange(-20.0, length_m + LANE_STEP_M, LANE_STEP_M)
    for side, key in (("upper", "upperLaneMarkings"), ("lower", "lowerLaneMarkings")):
        ys = sorted(float(v) for v in str(meta[key]).split(";") if v.strip())
        for i in range(len(ys) - 1):
            yc = (ys[i] + ys[i + 1]) / 2
            low_line = "solid" if i == 0 else "broken"  # marking at the smaller y
            high_line = "solid" if i == len(ys) - 2 else "broken"
            fwd = side == "lower"  # lower lanes drive +x; with y down, the driver's left is the smaller y
            pts = [[float(x), yc] for x in (xs if fwd else xs[::-1])]
            lanes.append({"id": f"{side}_{i}", "centreline": pts, "width_m": round(ys[i + 1] - ys[i], 2),
                          "lane_type": "driving", "junction": False, "speed_limit_kmh": limit_kmh,
                          "left_line": low_line if fwd else high_line, "right_line": high_line if fwd else low_line})
    return {"scene": f"highD_{meta['id']}", "coords": "highd_image_m", "left_handed": True, "lanes": lanes,
            "zones": [], "stop_lines": []}


def speeding_truth(tracks: dict, limit_kmh: float | None, min_s: float) -> set[int]:
    """Tracks whose dataset speed stays above limit + tolerance for >= min_s (reference truth)."""
    if limit_kmh is None:
        return set()
    thr = limit_kmh + speed_tolerance_kmh(limit_kmh, DEFAULTS["speeding"]["tolerance"])
    out = set()
    for tid, tr in tracks.items():
        over = tr["v"] > thr
        start = None
        for i, o in enumerate(over):
            if o and start is None:
                start = i
            if (not o or i == len(over) - 1) and start is not None:
                end = i if o else i - 1
                if tr["t"][end] - tr["t"][start] >= min_s:
                    out.add(tid)
                    break
                start = None
    return out


# --- inD / rounD ----------------------------------------------------------------------------------

def ind_recording(root: Path, rid: str, osm: Path) -> tuple[dict, dict, dict]:
    meta = read_csv(root / f"{rid}_recordingMeta.csv")[0]
    tmeta = {int(r["trackId"]): r for r in read_csv(root / f"{rid}_tracksMeta.csv")}
    fps = float(meta["frameRate"])
    cols = defaultdict(lambda: defaultdict(list))
    for r in read_csv(root / f"{rid}_tracks.csv"):
        tid = int(r["trackId"])
        if CLASSES.get(str(tmeta[tid]["class"]).lower()) is None:
            continue
        c = cols[tid]
        c["t"].append(int(r["frame"]) / fps)
        c["x"].append(float(r["xCenter"]))
        c["y"].append(float(r["yCenter"]))
    tracks = {tid: {"cls": CLASSES[str(tmeta[tid]["class"]).lower()], **{k: np.array(v) for k, v in c.items()}}
              for tid, c in cols.items()}
    limit = float(meta.get("speedLimit", -1) or -1)
    limit_kmh = None if limit <= 0 else round(limit * 3.6, 1)
    origin = (float(meta["xUtmOrigin"]), float(meta["yUtmOrigin"]))
    scene = lanelet_scene(osm, origin, limit_kmh)
    return tracks, scene, {"fps": fps, "speed_limit_kmh": limit_kmh, "duration_s": float(meta.get("duration", 0) or 0)}


def _line_type(tags: dict) -> str:
    t, sub = tags.get("type", ""), tags.get("subtype", "")
    if t in ("line_thin", "line_thick"):
        return {"solid": "solid", "dashed": "broken", "solid_solid": "solidsolid", "dashed_solid": "brokensolid",
                "solid_dashed": "solidbroken"}.get(sub, "other")
    return {"curbstone": "curb", "virtual": "none", "road_border": "curb"}.get(t, "other")


def _resample(line: np.ndarray, n: int) -> np.ndarray:
    seg = np.hypot(*np.diff(line, axis=0).T)
    s = np.r_[0.0, np.cumsum(seg)]
    u = np.linspace(0.0, s[-1], n)
    return np.c_[np.interp(u, s, line[:, 0]), np.interp(u, s, line[:, 1])]


def lanelet_scene(osm: Path, origin: tuple[float, float], limit_kmh: float | None) -> dict:
    """Lane map from a Lanelet2 .osm (nodes in lat/lon, or local_x/local_y tags) in the recording's
    local metres (UTM - origin)."""
    import utm
    root = ET.parse(osm).getroot()
    nodes = {}
    for n in root.iter("node"):
        tags = {t.get("k"): t.get("v") for t in n.iter("tag")}
        if "local_x" in tags and "local_y" in tags:
            nodes[n.get("id")] = (float(tags["local_x"]), float(tags["local_y"]))
        else:
            e, no, _, _ = utm.from_latlon(float(n.get("lat")), float(n.get("lon")))
            nodes[n.get("id")] = (e - origin[0], no - origin[1])
    ways = {}
    for w in root.iter("way"):
        tags = {t.get("k"): t.get("v") for t in w.iter("tag")}
        ways[w.get("id")] = (np.array([nodes[nd.get("ref")] for nd in w.iter("nd")]), _line_type(tags))
    lanes = []
    for rel in root.iter("relation"):
        tags = {t.get("k"): t.get("v") for t in rel.iter("tag")}
        if tags.get("type") != "lanelet" or tags.get("subtype", "road") not in ("road", "highway"):
            continue
        roles = {m.get("role"): m.get("ref") for m in rel.iter("member") if m.get("type") == "way"}
        if "left" not in roles or "right" not in roles:
            continue
        (left, ltype), (right, rtype) = ways[roles["left"]], ways[roles["right"]]
        if len(left) < 2 or len(right) < 2:
            continue
        if np.dot(left[-1] - left[0], right[-1] - right[0]) < 0:  # bounds drawn in opposite orders
            right = right[::-1]
        n = max(len(left), len(right), 2)
        lr, rr = _resample(left, n), _resample(right, n)
        centre = (lr + rr) / 2
        lanes.append({"id": f"ll{rel.get('id')}", "centreline": np.round(centre, 3).tolist(),
                      "width_m": round(float(np.median(np.hypot(*(lr - rr).T))), 2), "lane_type": "driving",
                      "junction": False, "speed_limit_kmh": limit_kmh, "left_line": ltype, "right_line": rtype,
                      "_poly": shapely.Polygon(np.r_[lr, rr[::-1]]).buffer(0)})
    for a in lanes:  # overlapping lanelets: intersection / roundabout interior
        others = [b["_poly"] for b in lanes if b is not a]
        if others and a["_poly"].area > 0:
            inter = a["_poly"].intersection(shapely.union_all(others)).area
            a["junction"] = inter / a["_poly"].area >= JUNCTION_OVERLAP
    for a in lanes:
        del a["_poly"]
    return {"scene": osm.stem, "coords": "levelx_local_m", "left_handed": False, "lanes": lanes,
            "zones": [], "stop_lines": []}


# --- run ------------------------------------------------------------------------------------------

def run_recording(dataset: str, root: Path, rid: str, osm: Path | None, hz: float, out_root: Path) -> dict:
    if dataset == "highd":
        tracks, scene_data, meta = highd_recording(root, rid)
    else:
        if osm is None:
            raise SystemExit(f"{dataset} needs --osm (the location's Lanelet2 map)")
        tracks, scene_data, meta = ind_recording(root, rid, osm)
    rows = smooth_rows(tracks, hz)
    scene = SceneMap(scene_data)
    events = Engine(scene, prefix=f"{dataset}{rid}").run(rows)
    out = out_root / f"{dataset}_{rid}"
    write_events(events, out)
    hours = (max(r["time_s"] for r in rows) - min(r["time_s"] for r in rows)) / 3600 if rows else 0.0
    counted = Counter(e.type for e in events if e.status in COUNTED_STATUS)
    summary = {"dataset": dataset, "recording": rid, "tracks": len(tracks), "rows": len(rows), "hz": hz,
               "hours": round(hours, 3), "vehicle_hours": round(sum(t["t"][-1] - t["t"][0] for t in tracks.values()) / 3600, 2),
               "speed_limit_kmh": meta["speed_limit_kmh"],
               "rows_on_a_lane": round(sum(scene.match(r["x"], r["y"]) is not None for r in rows[::10]) / max(len(rows[::10]), 1), 3),
               "events_by_type_status": {f"{t}/{s}": n for (t, s), n in sorted(Counter((e.type, e.status) for e in events).items())},
               "counted_per_hour": {t: round(n / hours, 1) for t, n in counted.items()} if hours else {}}
    if dataset == "highd":
        truth = speeding_truth(tracks, meta["speed_limit_kmh"], DEFAULTS["speeding"]["min_s"])
        flagged = {tid for e in events if e.type == "speeding" and e.status in COUNTED_STATUS for tid in e.track_ids}
        tp, fp, fn = len(truth & flagged), len(flagged - truth), len(truth - flagged)
        summary["speeding_vs_dataset_speeds"] = {
            "truth_tracks": len(truth), "flagged_tracks": len(flagged), "tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None}
        summary["solid_line_crossings"] = sum(1 for e in events if "solid_line_crossing" in e.tags)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=str.lower, choices=["highd", "ind", "round"])
    ap.add_argument("data", type=Path, help="The dataset's data/ folder (<id>_tracks.csv ...)")
    ap.add_argument("--recordings", nargs="+", required=True, help="Recording ids, e.g. 01 02")
    ap.add_argument("--osm", type=Path, default=None, help="inD/rounD: the location's Lanelet2 .osm map")
    ap.add_argument("--hz", type=float, default=10.0, help="Rate the engine runs at (rows per second per track)")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    for rid in args.recordings:
        s = run_recording(args.dataset, args.data, rid, args.osm, args.hz, args.out)
        print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
