"""Find the largest junction on the currently loaded map — on Town03 this is
the roundabout, by a wide margin (it's the biggest single junction in any
stock CARLA town). Useful for aiming the drone, or as `center` in the
traffic-concentration snippet in docs/Ground_Truth_Creation_Guide.md.

Run with CarlaAir already loaded on the map you want to check:
    python find_roundabout.py
    python find_roundabout.py --top 3   # show the 3 largest junctions, not just 1
"""

import argparse

import carla


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--top", type=int, default=1, help="How many of the largest junctions to print")
    args = ap.parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(args.timeout)
    world = client.get_world()
    print(f"[carla] connected, map = {world.get_map().name}")

    waypoints = world.get_map().generate_waypoints(3.0)
    junctions: dict[int, list[carla.Location]] = {}
    for wp in waypoints:
        if wp.is_junction:
            junctions.setdefault(wp.get_junction().id, []).append(wp.transform.location)

    if not junctions:
        raise SystemExit("No junctions found on this map.")

    ranked = sorted(junctions.items(), key=lambda kv: len(kv[1]), reverse=True)
    for jid, locs in ranked[: args.top]:
        cx = sum(l.x for l in locs) / len(locs)
        cy = sum(l.y for l in locs) / len(locs)
        cz = sum(l.z for l in locs) / len(locs)
        xs, ys = [l.x for l in locs], [l.y for l in locs]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        print(f"junction id={jid}: {len(locs)} waypoints, center=({cx:.1f}, {cy:.1f}, {cz:.1f}), "
              f"span~{span:.0f} m")

    print("\nFly the drone to the top result's (x, y) — that's the biggest junction on this map "
          "(the roundabout, on Town03).")


if __name__ == "__main__":
    main()
