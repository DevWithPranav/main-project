"""Violation overlay video (Violation Engine, Layer 7; Phase A step A9).

docs/Violation_Engine_Architecture.md, Section 5.8. Draws on a recorded CARLA flight:
  - zones from the scene (no-parking red, crosswalk white, no-U-turn orange, highway blue,
    speed zones purple), projected from the ground into every frame with the camera pose
  - every tracked vehicle: box, ID and smoothed speed (km/h) from kinematics.csv
  - a rule that is timing a vehicle (between an event's start and its flag): amber box,
    "checking <type>"
  - a flagged violation: thick red box with the type and its value until the event ends
  - HUD: time, violations flagged so far per type, the ones open now

--clips writes one evidence clip per event instead (Section 5.7, human review): 7 s before
to 3 s after the flag, with a banner naming the event, into <violations>/events/<event_id>.mp4,
and records each clip as the event's `evidence` in violations.json / .csv.

Inputs come from run_violations.py (violations.json + kinematics.csv) and the pipeline's
trajectories_final.csv (pixel boxes).

Usage:
    python ml/violation_engine/render_violations.py <flight dir> --scene ml/violation_engine/configs/scenes/Town05.json
    python ml/violation_engine/render_violations.py --site <site.json> --trajectories <clip>/trajectories_final.csv
    python ml/violation_engine/render_violations.py <flight dir> --scene ... --zones <scenario_log.json> --events-only
    python ml/violation_engine/render_violations.py <flight dir> --scene ... --clips
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

from events import COUNTED_STATUS, attach_evidence
from ground_coords import BOX_CENTRE_Z, FlightCamera
from make_demo_videos import draw_hud, id_color
from run_violations import RESULTS_DIR, merge_zones
from stitch_tracklets import iter_frames

ZONE_STYLE = {"no_parking": (40, 40, 230), "crosswalk": (235, 235, 235), "no_u_turn": (0, 140, 255),
              "highway": (230, 140, 40), "speed": (200, 60, 170)}
AMBER, RED = (0, 190, 255), (30, 30, 230)
LABEL = {"no_parking": "NO PARKING", "wrong_way": "WRONG WAY", "illegal_u_turn": "ILLEGAL U-TURN",
         "speeding": "SPEEDING", "lane_violation": "LANE VIOLATION", "zebra_crossing": "ON ZEBRA CROSSING",
         "highway_stop": "HIGHWAY STOP", "red_light": "RED LIGHT"}


def event_value(e: dict) -> str:
    v = e.get("value", {})
    if "max_speed_kmh" in v and "limit_kmh" in v:
        return f"{v['max_speed_kmh']:.0f} in {v['limit_kmh']:.0f} km/h"
    if "pedestrian_track_ids" in v:  # F2 / F3 / F5 (zebra_pedestrians.py)
        gap = v.get("min_gap_m")
        return "pedestrian" + (f" {gap:.1f} m away" if gap is not None else " waiting")
    if "dwell_s" in v:
        return f"{v['dwell_s']:.0f} s"
    if "distance_m" in v:
        return f"{v['distance_m']:.0f} m"
    if "turn_deg" in v:
        return f"{abs(v['turn_deg']):.0f} deg"
    if "line" in v:
        return f"{v['line']} line"
    if "red_for_s" in v:
        return f"{v['red_for_s']:.1f} s into red"
    return ""


def label_box(img, p0, p1, color, text, thick=2, scale=0.5):
    cv2.rectangle(img, p0, p1, color, thick)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    ty = max(p0[1], th + 6)
    cv2.rectangle(img, (p0[0], ty - th - 6), (p0[0] + tw + 4, ty), color, -1)
    cv2.putText(img, text, (p0[0] + 2, ty - 4), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 1)


def draw_zones(img, cam: FlightCamera, frame: int, zones: list, road_z: float) -> None:
    overlay = img.copy()
    drew = False
    for z in zones:
        poly = np.array(z["polygon"], float)
        uv = cam.to_pixels(frame, np.c_[poly, np.full(len(poly), road_z)])
        if uv is None or np.isnan(uv).any():
            continue
        h, w = img.shape[:2]
        if (uv[:, 0].max() < 0 or uv[:, 0].min() > w or uv[:, 1].max() < 0 or uv[:, 1].min() > h):
            continue
        pts = uv.round().astype(np.int32)
        color = ZONE_STYLE.get(z["type"], (200, 200, 200))
        cv2.fillPoly(overlay, [pts], color)
        cv2.polylines(img, [pts], True, color, 2)
        drew = True
    if drew:
        cv2.addWeighted(overlay, 0.25, img, 0.75, 0, dst=img)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path, nargs="?", default=None, help="Recorded CARLA flight folder (frames/, camera poses)")
    ap.add_argument("--scene", type=Path, default=None, help="CARLA lane map (with a flight)")
    ap.add_argument("--site", type=Path, default=None,
                    help="Real footage: the clip's site file; needs --trajectories; zones from <violations>/scene.json")
    ap.add_argument("--zones", type=Path, default=None, help="Extra zones (e.g. a scenario log)")
    ap.add_argument("--violations", type=Path, default=None,
                    help="run_violations.py output folder (default: the pipeline's, under recorded_flight_validation)")
    ap.add_argument("--trajectories", type=Path, default=None, help="Pixel boxes (default: trajectories_final.csv)")
    ap.add_argument("--zone-types", nargs="*", default=["no_parking", "crosswalk", "no_u_turn", "highway", "speed"])
    ap.add_argument("--events-only", action="store_true", help="Only the stretches around events (+-pad s)")
    ap.add_argument("--pad", type=float, default=4.0)
    ap.add_argument("--clips", action="store_true",
                    help="Evidence clips: one mp4 per event in <violations>/events/, paths written into violations.json")
    ap.add_argument("--clip-before", type=float, default=7.0, help="Seconds before the flag in each clip")
    ap.add_argument("--clip-after", type=float, default=3.0, help="Seconds after the flag (10 s clips by default)")
    ap.add_argument("--scale", type=float, default=0.75, help="Output size relative to the frames")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.site:
        from real_geometry import RealCamera, Site
        if args.trajectories is None:
            raise SystemExit("--site needs --trajectories")
        traj = args.trajectories
        vdir = args.violations or traj.parent / "violations"
        site = Site(args.site)
        cam = RealCamera(site, traj.with_name("scene_map.npz"))
        scene = json.loads((vdir / "scene.json").read_text())
        source, road_z = site.video, 0.0
        cap = cv2.VideoCapture(str(site.video))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.release()
    else:
        if args.flight is None or args.scene is None:
            raise SystemExit("give a CARLA flight folder with --scene, or --site with --trajectories")
        base = RESULTS_DIR / args.flight.name / "tracktrack_ours"
        vdir = args.violations or base / "violations"
        traj = args.trajectories or base / "trajectories_final.csv"
        scene = merge_zones(json.loads(args.scene.read_text()), args.zones)
        cam = FlightCamera(args.flight)
        road_z = cam.ground_z - BOX_CENTRE_Z
        source = args.flight / "frames"
        fps = json.loads((args.flight / "metadata.json").read_text()).get("avg_fps", 25.0)
    events = [e for e in json.loads((vdir / "violations.json").read_text())
              if e["status"] in COUNTED_STATUS or e["status"] == "possible_breakdown"]
    zones = [z for z in scene.get("zones", []) if z["type"] in args.zone_types]

    # kinematics piece ids -> source track ids, and per-frame speed of each source track
    piece_src, speed = {}, {}
    with open(vdir / "kinematics.csv", newline="") as f:
        for r in csv.DictReader(f):
            src = int(r.get("src_track_id") or r["track_id"])
            piece_src[int(r["track_id"])] = src
            if r.get("visible", "1") == "1":  # only speeds backed by real measurements (not cut boxes / extrapolation)
                speed[(int(r["frame"]), src)] = float(r["speed_kmh"])
    for e in events:
        e["src_ids"] = {piece_src.get(t, t) for t in e["track_ids"]}
    boxes = defaultdict(list)
    with open(traj, newline="") as f:
        for r in csv.DictReader(f):
            boxes[int(r["frame"])].append(r)

    flagged_so_far = Counter()
    seen_flags = set()

    def annotate(img, fi):
        draw_zones(img, cam, fi, zones, road_z)
        active = [e for e in events if e["start_frame"] <= fi <= (e["end_frame"] or e["flag_frame"])]
        state = {}  # src id -> (color, text, thick)
        for e in active:
            flagged = fi >= e["flag_frame"]
            if flagged and e["event_id"] not in seen_flags:
                seen_flags.add(e["event_id"])
                flagged_so_far[e["type"]] += 1
            text = (f"{LABEL.get(e['type'], e['type'])} {event_value(e)}" if flagged
                    else f"checking {LABEL.get(e['type'], e['type']).lower()}")
            for sid in e["src_ids"]:
                if flagged or sid not in state:
                    state[sid] = (RED if flagged else AMBER, text, 4 if flagged else 3)
        # gap-filled boxes (interp) are straight lines in pixels while the camera moves, not
        # measurements, so they never carry an event label; the spot ring marks the car instead
        drawn = {int(r["track_id"]) for r in boxes.get(fi, []) if r.get("interp", "0") != "1"}
        for e in active:  # a stop-type event whose car has no box this frame: mark its spot
            if e.get("zone_id") and not (e["src_ids"] & drawn):
                uv = cam.to_pixels(fi, np.array([[e["x"], e["y"], road_z]]))
                if uv is not None and not np.isnan(uv).any():
                    c = tuple(int(v) for v in uv[0])
                    flagged = fi >= e["flag_frame"]
                    color = RED if flagged else AMBER
                    cv2.circle(img, c, 38, color, 4 if flagged else 3)
                    text = (f"{LABEL.get(e['type'], e['type'])} {event_value(e)}" if flagged
                            else f"checking {LABEL.get(e['type'], e['type']).lower()}")
                    label_box(img, (c[0] - 38, c[1] - 38), (c[0] - 38, c[1] - 38), color, text + "  [spot]", 1, 0.6)
        for r in boxes.get(fi, []):
            tid = int(r["track_id"])
            cx, cy, w, h = (float(r[k]) for k in ("cx", "cy", "w", "h"))
            p0, p1 = (int(cx - w / 2), int(cy - h / 2)), (int(cx + w / 2), int(cy + h / 2))
            v = speed.get((fi, tid))
            text = f"{tid}" + (f"  {v:.0f} km/h" if v is not None else "")
            if r.get("interp", "0") == "1":
                label_box(img, p0, p1, id_color(tid), f"{tid}", 1)
            elif tid in state:
                color, etext, thick = state[tid]
                label_box(img, p0, p1, color, f"{etext}  [{text}]", thick, 0.6)
            else:
                label_box(img, p0, p1, id_color(tid), text)
        lines = [f"t = {fi / fps:6.1f} s   frame {fi}",
                 "violations flagged: " + (", ".join(f"{LABEL[k].lower()} {n}" for k, n in sorted(flagged_so_far.items())) or "none")]
        lines += [f"> {LABEL.get(e['type'], e['type'])}  vehicle {sorted(e['src_ids'])[0]}  {event_value(e)}"
                  for e in active if fi >= e["flag_frame"]][:4]
        draw_hud(img, lines)
        if args.scale != 1.0:
            img = cv2.resize(img, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_AREA)
        return img

    if args.clips:
        write_clips(source, events, annotate, fps, vdir, args.clip_before, args.clip_after)
        return

    keep = None
    if args.events_only:
        keep = set()
        pad = int(args.pad * fps)
        for e in events:
            keep.update(range(max(0, e["start_frame"] - pad), (e["end_frame"] or e["flag_frame"]) + pad + 1))

    out = args.out or vdir / ("violations_events.mp4" if args.events_only else "violations.mp4")
    writer = None
    for fi, img in enumerate(iter_frames(source)):
        if keep is not None and fi not in keep:
            if fi > max(keep):
                break
            continue
        img = annotate(img, fi)
        if writer is None:
            writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (img.shape[1], img.shape[0]))
        writer.write(img)
    if writer is not None:
        writer.release()
    print(f"[render] {len(events)} events, {len(zones)} zones -> {out}")


def write_clips(source: Path, events: list[dict], annotate, fps: float, vdir: Path,
                before_s: float, after_s: float) -> None:
    """One evidence clip per event (Section 5.7, human review): before_s .. after_s around the
    flag, the full overlay plus a banner naming the event. One pass over the frames; the clip
    paths are written back into violations.json / .csv as each event's `evidence`."""
    windows = {e["event_id"]: (max(0, e["flag_frame"] - int(before_s * fps)), e["flag_frame"] + int(after_s * fps))
               for e in events}
    if not windows:
        print("[clips] no events")
        return
    last = max(f1 for _, f1 in windows.values())
    clip_dir = vdir / "events"
    clip_dir.mkdir(exist_ok=True)
    by_id = {e["event_id"]: e for e in events}
    writers, frames_written = {}, Counter()
    for fi, img in enumerate(iter_frames(source)):
        if fi > last:
            break
        open_now = [eid for eid, (f0, f1) in windows.items() if f0 <= fi <= f1]
        if not open_now:
            continue
        img = annotate(img, fi)
        for eid in open_now:
            frame = img.copy()
            draw_banner(frame, by_id[eid], fi, fps)
            if eid not in writers:
                writers[eid] = cv2.VideoWriter(str(clip_dir / f"{eid}.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                               (frame.shape[1], frame.shape[0]))
            writers[eid].write(frame)
            frames_written[eid] += 1
            if fi == windows[eid][1]:
                writers.pop(eid).release()
    for w in writers.values():  # windows that run past the last frame
        w.release()
    evidence = {eid: {"frame": by_id[eid]["flag_frame"], "clip": f"events/{eid}.mp4"} for eid in frames_written}
    attach_evidence(vdir, evidence)
    print(f"[clips] {len(evidence)} clips ({sum(frames_written.values())} frames) -> {clip_dir}")


def draw_banner(img, e: dict, fi: int, fps: float) -> None:
    """Bottom banner of an evidence clip: what the reviewer is looking at and when it was flagged."""
    h, w = img.shape[:2]
    flagged = fi >= e["flag_frame"]
    lines = [f"EVIDENCE {e['event_id']}   {LABEL.get(e['type'], e['type'])} {event_value(e)}   "
             f"vehicle {sorted(e['src_ids'])[0]}",
             f"{e['status']}   conf {e['confidence']:.2f}" + (f"   tags: {', '.join(e['tags'])}" if e["tags"] else ""),
             f"flag at frame {e['flag_frame']}" + ("   FLAGGED" if flagged else
                                                    f"   flags in {(e['flag_frame'] - fi) / fps:.1f} s")]
    font = max(0.3, 0.7 * w / 1440)  # 0.7 on a 1440 px wide clip (1080p at --scale 0.75)
    lh, thick = int(round(43 * font)), 1 if font < 0.5 else 2
    top = h - lh * len(lines) - int(14 * font)
    overlay = img.copy()
    cv2.rectangle(overlay, (0, top), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.65, img, 0.35, 0, img)
    for i, t in enumerate(lines):
        cv2.putText(img, t, (int(12 * font / 0.7), top + lh * (i + 1) - int(8 * font)), cv2.FONT_HERSHEY_SIMPLEX, font,
                    RED if flagged and i == len(lines) - 1 else (255, 255, 255), thick)


if __name__ == "__main__":
    main()
