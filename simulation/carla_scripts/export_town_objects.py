"""Export a CARLA town's static objects (buildings, trees, poles, signs, fences, bridges, rail, ...)
as oriented 3D boxes for the digital twin (Build Plan M7) and the stager's view-blocking check.

CARLA's packaged build doesn't expose the meshes, but world.get_environment_objects() gives every
static object's label and world-space oriented bounding box: the exact layout and massing of the
town. Roads, lane markings, sidewalks and terrain are left out (the lane map draws the roads),
as are map-baked vehicles (they are not part of the town).

Output: ml/violation_engine/configs/scenes/<Town>_objects.json
    {"scene": "Town03", "source": "...", "labels": [...],
     "objects": [{"l": label index, "c": [x, y, z], "e": [ex, ey, ez], "r": [pitch, yaw, roll]}, ...]}
    c = box centre (CARLA world metres), e = half extents (m), r = rotation (deg).

Usage (venv_sim, CarlaAir running; loads the town first if needed):
    python simulation/carla_scripts/export_town_objects.py Town03
"""

import argparse
import json
import time
from pathlib import Path

import carla

OUT_DIR = Path(__file__).resolve().parents[2] / "ml" / "violation_engine" / "configs" / "scenes"
SKIP = {"Roads", "RoadLines", "Sidewalks", "Ground", "Terrain", "Water", "Sky", "NONE", "Any", "Unlabeled",
        "Car", "Truck", "Bus", "Motorcycle", "Bicycle", "Rider", "Pedestrians", "Train", "Other"}
MIN_EXTENT_M = 0.05  # boxes thinner than this in every axis are decals, not objects


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("town")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    a = ap.parse_args()

    client = carla.Client(a.host, a.port)
    client.set_timeout(240)
    world = client.get_world()
    if not world.get_map().name.endswith(a.town):
        print(f"[objects] loading {a.town}")
        world = client.load_world(a.town)
        time.sleep(5)
    objs = world.get_environment_objects(carla.CityObjectLabel.Any)
    labels: list[str] = []
    out, skipped = [], {}
    for o in objs:
        lab = str(o.type)
        if lab in SKIP:
            skipped[lab] = skipped.get(lab, 0) + 1
            continue
        bb = o.bounding_box
        e = [bb.extent.x, bb.extent.y, bb.extent.z]
        if max(e) < MIN_EXTENT_M:
            continue
        if lab not in labels:
            labels.append(lab)
        out.append({"l": labels.index(lab),
                    "c": [round(bb.location.x, 2), round(bb.location.y, 2), round(bb.location.z, 2)],
                    "e": [round(v, 2) for v in e],
                    "r": [round(bb.rotation.pitch, 1), round(bb.rotation.yaw, 1), round(bb.rotation.roll, 1)]})
    counts = {lab: sum(1 for x in out if x["l"] == i) for i, lab in enumerate(labels)}
    doc = {"scene": a.town, "source": "carla.World.get_environment_objects (oriented bounding boxes)",
           "coords": "carla_world_m", "labels": labels, "counts": counts, "skipped": skipped, "objects": out}
    path = OUT_DIR / f"{a.town}_objects.json"
    path.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"[objects] {len(out)} objects ({counts}); skipped {skipped} -> {path} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
