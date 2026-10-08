"""Draw a real clip's lanes, zones and calibration by clicking (Violation Engine, Phase B2).

docs/Violation_Engine_Architecture.md, Section 5.4 (real footage). Opens the clip's
reference frame (the first frame by default - the frame scene_map.py anchors the map to)
and writes a site file for real_geometry.py:

  l  lane         click the centreline IN THE DRIVING DIRECTION, Enter; then type width (m),
                  speed limit (km/h), left/right line type, lane type, junction (y/n) in the console
  z  zone         click the polygon, Enter; then type the zone type (no_parking / crosswalk /
                  no_u_turn / highway / speed) and its value (grace_s or limit_kmh)
  c  calibration  2 points + Enter -> type the real length between them (m) ("scale", camera
                  looking down); or 4+ points + Enter -> type each point's ground x y in metres
                  ("homography", tilted camera), e.g. the corners of a lane rectangle
  Enter           finish the current shape         u  undo last point / last shape
  s  save         q  save and quit                 Esc  quit without saving

Shapes already in the site file are drawn and kept (open the same file again to edit it).
Lanes show an arrow in their driving direction; check it before saving: a lane drawn the
wrong way round makes every car on it a wrong-way driver.

  --preview out.png    draw the site file on the reference frame and save it (no window)

Usage:
    python ml/violation_engine/zone_tool.py ml/violation_engine/configs/sites/roundabout.json --video "<clip>.mp4"
    python ml/violation_engine/zone_tool.py ml/violation_engine/configs/sites/roundabout.json --preview check.png
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
ZONE_COLORS = {"no_parking": (40, 40, 230), "crosswalk": (235, 235, 235), "no_u_turn": (0, 140, 255),
               "highway": (230, 140, 40), "speed": (200, 60, 170)}
LANE_COLOR, CAL_COLOR, DRAFT_COLOR = (60, 220, 60), (0, 230, 255), (255, 0, 255)
MAX_VIEW_W = 1600


def read_frame(video: Path, index: int) -> np.ndarray:
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, img = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"cannot read frame {index} of {video}")
    return img


def draw_site(img: np.ndarray, site: dict, scale: float = 1.0) -> np.ndarray:
    out = img.copy()
    overlay = out.copy()
    s = lambda pts: (np.array(pts, float) * scale).round().astype(np.int32)  # noqa: E731
    for z in site.get("zones", []):
        pts = s(z["polygon_px"])
        color = ZONE_COLORS.get(z["type"], (200, 200, 200))
        cv2.fillPoly(overlay, [pts], color)
        cv2.polylines(out, [pts], True, color, 2)
        cv2.putText(out, f"{z['id']} ({z['type']})", tuple(pts.min(axis=0)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    out = cv2.addWeighted(overlay, 0.25, out, 0.75, 0)
    for l in site.get("lanes", []):
        pts = s(l["centreline_px"])
        cv2.polylines(out, [pts], False, LANE_COLOR, 2)
        for a, b in zip(pts[:-1], pts[1:]):  # arrows show the driving direction
            mid = ((a + b) / 2).astype(int)
            d = b - a
            n = max(np.hypot(*d), 1e-6)
            tip = (mid + d / n * 14).astype(int)
            cv2.arrowedLine(out, tuple(mid), tuple(tip), LANE_COLOR, 2, tipLength=0.6)
        label = f"{l['id']} {l.get('speed_limit_kmh', '')}km/h {l.get('left_line', '')}|{l.get('right_line', '')}"
        cv2.putText(out, label, tuple(pts[0] + [4, -6]), cv2.FONT_HERSHEY_SIMPLEX, 0.45, LANE_COLOR, 1)
    cal = site.get("calibration")
    if cal:
        pts = s(cal["image_points"])
        for i, p in enumerate(pts):
            cv2.circle(out, tuple(p), 5, CAL_COLOR, -1)
            tag = f"{cal['world_points'][i]}" if cal["method"] == "homography" else str(i)
            cv2.putText(out, tag, tuple(p + [6, -6]), cv2.FONT_HERSHEY_SIMPLEX, 0.45, CAL_COLOR, 1)
        if cal["method"] == "scale":
            cv2.line(out, tuple(pts[0]), tuple(pts[1]), CAL_COLOR, 2)
            cv2.putText(out, f"{cal['length_m']} m", tuple(((pts[0] + pts[1]) / 2).astype(int)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, CAL_COLOR, 2)
        else:
            cv2.polylines(out, [pts], True, CAL_COLOR, 1)
    return out


def ask(prompt: str, default=None, cast=str):
    raw = input(f"{prompt}{f' [{default}]' if default is not None else ''}: ").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        print("  not understood, using the default")
        return default


def finish(site: dict, mode: str, pts: list) -> None:
    if mode == "lane" and len(pts) >= 2:
        n = len(site["lanes"]) + 1
        site["lanes"].append({
            "id": ask("lane id", f"L{n}"), "centreline_px": pts,
            "width_m": ask("width (m)", 3.5, float), "speed_limit_kmh": ask("speed limit (km/h)", 50.0, float),
            "left_line": ask("left line (solid/broken/solidsolid/none)", "broken"),
            "right_line": ask("right line (solid/broken/solidsolid/none)", "broken"),
            "lane_type": ask("lane type (driving/shoulder/parking)", "driving"),
            "junction": ask("inside a junction/roundabout? (y/n)", "n").lower().startswith("y")})
    elif mode == "zone" and len(pts) >= 3:
        ztype = ask("zone type (no_parking/crosswalk/no_u_turn/highway/speed)", "no_parking")
        z = {"id": ask("zone id", f"{ztype}_{len(site['zones']) + 1}"), "type": ztype, "polygon_px": pts}
        if ztype in ("no_parking", "highway"):
            z["grace_s"] = ask("grace (s)", 30.0 if ztype == "no_parking" else 20.0, float)
        if ztype == "speed":
            z["limit_kmh"] = ask("limit (km/h)", 30.0, float)
        site["zones"].append(z)
    elif mode == "calibration" and len(pts) == 2:
        site["calibration"] = {"method": "scale", "image_points": pts,
                               "length_m": ask("real length between the 2 points (m)", 3.5, float)}
    elif mode == "calibration" and len(pts) >= 4:
        world = []
        for i, p in enumerate(pts):
            xy = ask(f"ground x y (m) of point {i} at pixel {p}", "0 0")
            world.append([float(v) for v in xy.replace(",", " ").split()[:2]])
        site["calibration"] = {"method": "homography", "image_points": pts, "world_points": world}
    else:
        print(f"[tool] not enough points for a {mode} ({len(pts)}); discarded")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", type=Path, help="Site file to create or edit")
    ap.add_argument("--video", type=Path, default=None, help="The clip (needed when creating a new site)")
    ap.add_argument("--frame", type=int, default=None, help="Reference frame (default: the site's, else 0)")
    ap.add_argument("--preview", type=Path, default=None, help="Draw the site on the frame, save to this image, exit")
    args = ap.parse_args()

    site = json.loads(args.site.read_text()) if args.site.exists() else {"lanes": [], "zones": []}
    site.setdefault("lanes", [])
    site.setdefault("zones", [])
    if args.video:
        v = args.video.resolve()
        site["video"] = str(v.relative_to(REPO)) if v.is_relative_to(REPO) else str(v)
    if "video" not in site:
        raise SystemExit("new site: give --video")
    video = REPO / site["video"] if not Path(site["video"]).is_absolute() else Path(site["video"])
    site["reference_frame"] = args.frame if args.frame is not None else site.get("reference_frame", 0)
    img = read_frame(video, site["reference_frame"])
    site["frame_size"] = [img.shape[1], img.shape[0]]
    site.setdefault("ref_point", 0.5)

    if args.preview:
        cv2.imwrite(str(args.preview), draw_site(img, site))
        print(f"[tool] preview -> {args.preview}")
        return

    scale = min(1.0, MAX_VIEW_W / img.shape[1])
    view = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img
    state = {"mode": "lane", "pts": []}

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            state["pts"].append([round(x / scale, 1), round(y / scale, 1)])

    win = "zone_tool - l lane, z zone, c calibration, Enter finish, u undo, s save, q quit"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    saved = True
    while True:
        frame = draw_site(view, site, scale)
        if state["pts"]:
            p = (np.array(state["pts"]) * scale).round().astype(np.int32)
            cv2.polylines(frame, [p], state["mode"] == "zone", DRAFT_COLOR, 2)
            for q in p:
                cv2.circle(frame, tuple(q), 4, DRAFT_COLOR, -1)
        cv2.putText(frame, f"mode: {state['mode']}  points: {len(state['pts'])}", (10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, DRAFT_COLOR, 2)
        cv2.imshow(win, frame)
        k = cv2.waitKey(30) & 0xFF
        if k in (ord("l"), ord("z"), ord("c")):
            state.update(mode={"l": "lane", "z": "zone", "c": "calibration"}[chr(k)], pts=[])
        elif k == 13:  # Enter
            finish(site, state["mode"], state["pts"])
            state["pts"] = []
            saved = False
        elif k == ord("u"):
            if state["pts"]:
                state["pts"].pop()
            elif state["mode"] == "lane" and site["lanes"]:
                site["lanes"].pop()
            elif state["mode"] == "zone" and site["zones"]:
                site["zones"].pop()
            saved = False
        elif k in (ord("s"), ord("q")):
            args.site.parent.mkdir(parents=True, exist_ok=True)
            args.site.write_text(json.dumps(site, indent=1))
            print(f"[tool] saved {len(site['lanes'])} lanes, {len(site['zones'])} zones -> {args.site}")
            saved = True
            if k == ord("q"):
                break
        elif k == 27:
            if not saved:
                print("[tool] quit without saving")
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
