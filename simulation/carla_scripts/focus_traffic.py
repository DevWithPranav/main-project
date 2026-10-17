"""Concentrate traffic around a point instead of scattered map-wide (auto_traffic.py's and
spawn_traffic()'s spawn points are uniform-random over the whole map, so a roundabout or one
junction ends up with only a couple of vehicles in view — this measured ~2.4 vehicles/frame on
Town10HD, see Ground_Truth_Creation_Guide.md Section 4).

Safe to run **while a flight is being recorded**: it only spawns/destroys vehicle actors, never
touches the map or the drone/camera actors, so recording keeps running through it. One caveat:
vehicles spawned this way after `record_flight.py --labels` started are not in that recording's
vehicle_poses.csv (the pose logger's vehicle list is fixed at recording start), so
carla_autolabel.py falls back to its weaker occlusion estimate for them. If you can, run this
*before* starting the recorder; if traffic is already right, restart the recorder after this runs
so the new cars are tracked from frame 1.

Leaves auto_traffic.py's health-check loop running as is — it only re-enables autopilot on
vehicles by id (never respawns a destroyed one at a new point), so destroying/spawning here is
safe alongside it.

Buses (vehicle.mitsubishi.fusorosa, this build's only bus) turn badly at a tight roundabout and
repeatedly stall with a queue behind them — seen twice at Town03's roundabout. --no-buses (on by
default) leaves buses out of the spawn pool here; pass --allow-buses to put them back.

A busy roundabout can also **fully gridlock** even with no bus and no collision involved: this
Traffic Manager has no special roundabout right-of-way, so under enough density every entering
vehicle can end up perpetually waiting for a "clean" gap that never comes, locking the whole
junction (confirmed 2026-09-29 — removing the head-of-queue vehicles did nothing; only vehicles
elsewhere on the map, away from the roundabout, were still moving). Fix: every vehicle spawned
here gets `tm.ignore_vehicles_percentage(actor, --ignore-vehicles-pct)`, so it stops treating
ordinary traffic as a blocking hazard past that point and just goes. This does mean closer, more
assertive merging than default TM — acceptable for training footage; raise --ignore-vehicles-pct
if it locks up again, lower it if merges look too aggressive.

--watch keeps traffic concentrated for the length of a whole flight, not just once: CARLA's
Traffic Manager drives spawned vehicles wherever the road network takes them, so a one-off run
empties out again within seconds (~20 of 77 vehicles had already left a 130 m radius within 6 s
in testing). In watch mode this script instead loops, and every --interval seconds recycles
whatever has drifted outside the radius: destroys it and spawns a replacement at a spawn point
back near the center (with --no-buses re-applied each cycle, so a bus can't reappear from normal
respawning either). Run it in the background for the whole recording:
    start /min python focus_traffic.py --watch --interval 8      (Windows, detached)
or in its own terminal, left running, Ctrl+C to stop.

--watch competes with the flight controls for AirSim's attention (2026-09-29): each recycle pass
is a blocking batch spawn/destroy call, and AirSim's RPC server shares a thread with CARLA's own
processing, so a big pass can starve it — confirmed directly (ping 1ms with --watch off, 1-5000ms+
with it on at --interval 8, same otherwise). Destroys are now fire-and-forget (apply_batch, not
apply_batch_sync) and the default interval is longer, which should be enough headroom for normal
flying; if the controller still stutters, raise --interval further (15-20s) or stop --watch and
top up with a plain (non-watch) run every so often instead.

Usage (with CarlaAir already running):
    python focus_traffic.py                       # auto-find the biggest junction, like find_roundabout.py
    python focus_traffic.py --center -83.4 6.4     # explicit point (x, y)
    python focus_traffic.py --center -83.4 6.4 --radius 150 --vehicles 80
    python focus_traffic.py --watch --interval 8   # keep it concentrated continuously
"""

import argparse
import random
import time

import carla

BUS_BLUEPRINTS = {"vehicle.mitsubishi.fusorosa"}
SPAWN_STAGGER_S = 0.4  # seconds between each spawn in a pass — see spawn_at()'s docstring note


def find_biggest_junction_center(world) -> tuple[float, float]:
    waypoints = world.get_map().generate_waypoints(3.0)
    junctions: dict[int, list[carla.Location]] = {}
    for wp in waypoints:
        if wp.is_junction:
            junctions.setdefault(wp.get_junction().id, []).append(wp.transform.location)
    if not junctions:
        raise SystemExit("No junctions on this map — pass --center x y explicitly.")
    _, locs = max(junctions.items(), key=lambda kv: len(kv[1]))
    return sum(l.x for l in locs) / len(locs), sum(l.y for l in locs) / len(locs)


def lane_center_spawn_points(m: carla.Map, cx: float, cy: float, radius: float,
                             spacing: float = 12.0) -> list[carla.Transform]:
    """Spawn points along ordinary driving lanes within the radius, for roads (typically
    highways) with no registered spawn points of their own. Raised 0.3 m to avoid spawning
    inside the road mesh; skips junctions, since a vehicle mid-junction has no clear lane."""
    out = []
    for wp in m.generate_waypoints(spacing):
        if wp.is_junction or wp.lane_type != carla.LaneType.Driving:
            continue
        loc = wp.transform.location
        if (loc.x - cx) ** 2 + (loc.y - cy) ** 2 <= radius ** 2:
            tf = wp.transform
            out.append(carla.Transform(carla.Location(tf.location.x, tf.location.y, tf.location.z + 0.3),
                                       tf.rotation))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--center", type=float, nargs=2, metavar=("X", "Y"),
                    help="Point to concentrate traffic around; default: auto-find the biggest junction")
    ap.add_argument("--radius", type=float, default=120.0, help="Metres from --center to keep/spawn within")
    ap.add_argument("--vehicles", type=int, default=80, help="Target vehicle count inside the radius")
    ap.add_argument("--tm-port", type=int, default=8000, help="Traffic manager port (match auto_traffic.py)")
    ap.add_argument("--keep-outside", action="store_true",
                    help="Don't destroy vehicles currently outside the radius, only add more inside")
    ap.add_argument("--allow-buses", action="store_true",
                    help="Let buses spawn here too (off by default — they stall repeatedly at tight roundabouts)")
    ap.add_argument("--watch", action="store_true",
                    help="Keep looping instead of running once: recycle vehicles back near the center every "
                         "--interval seconds, for the whole recording")
    ap.add_argument("--interval", type=float, default=15.0,
                    help="--watch: seconds between recycle passes (was 8.0 — too frequent, competed with "
                         "the flight controls for AirSim's attention; see module docstring)")
    ap.add_argument("--max-spawn-per-pass", type=int, default=20,
                    help="Spawn at most this many vehicles in one pass — a full top-up in one blocking batch "
                         "is the other big contributor to AirSim lag; a large deficit is refilled gradually "
                         "over several passes instead")
    ap.add_argument("--min-gap", type=float, default=2.5,
                    help="Traffic Manager's minimum following distance (m)")
    ap.add_argument("--ignore-vehicles-pct", type=float, default=50.0,
                    help="Chance (0-100) a spawned vehicle ignores other traffic when deciding whether to go. "
                         "The real fix for roundabout gridlock (see module docstring); 0 disables it")
    ap.add_argument("--min-spawn-points", type=int, default=15,
                    help="If fewer than this many registered spawn points are within the radius (normal for a "
                         "highway stretch — CARLA puts spawn points on ordinary streets, not highway lanes), "
                         "generate extra ones directly from the lane centerline instead")
    args = ap.parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(args.timeout)
    world = client.get_world()
    print(f"[focus] connected, map = {world.get_map().name}")

    cx, cy = args.center if args.center else find_biggest_junction_center(world)
    print(f"[focus] center = ({cx:.1f}, {cy:.1f}), radius = {args.radius} m, buses "
          f"{'allowed' if args.allow_buses else 'excluded'}")

    m = world.get_map()
    near_points = [sp for sp in m.get_spawn_points()
                   if (sp.location.x - cx) ** 2 + (sp.location.y - cy) ** 2 <= args.radius ** 2]
    print(f"[focus] {len(near_points)} registered spawn points within {args.radius} m")
    if len(near_points) < args.min_spawn_points:
        extra = lane_center_spawn_points(m, cx, cy, args.radius)
        print(f"[focus] too few registered points — generated {len(extra)} more from the lane centerline "
              f"(e.g. a highway stretch, which has none of its own)")
        near_points += extra
    if not near_points:
        raise SystemExit("No spawn points that close, even generated ones — increase --radius.")

    bp_lib = world.get_blueprint_library()
    car_bps = [bp for bp in bp_lib.filter("vehicle.*") if int(bp.get_attribute("number_of_wheels")) == 4]
    if not args.allow_buses:
        car_bps = [bp for bp in car_bps if bp.id not in BUS_BLUEPRINTS]
    tm = client.get_trafficmanager(args.tm_port)
    tm.set_global_distance_to_leading_vehicle(args.min_gap)
    tm.global_percentage_speed_difference(10.0)

    def spawn_at(points: list) -> int:
        random.shuffle(points)
        # Two bugs found here (2026-10-01), both now worked around:
        # 1. SpawnActor(...).then(SetAutopilot(FutureActor, ...)) chained in one batch command is
        #    the documented CARLA pattern, but is unreliable on this build: vehicles spawned this
        #    way can sit with zero throttle indefinitely (confirmed not a density/gridlock issue —
        #    a single vehicle spawned the exact same way also never moved). Calling
        #    actor.set_autopilot(...) directly on the already-spawned actor works every time, so
        #    spawning is now a separate step from enabling autopilot.
        # 2. Spawning many vehicles at once, even with that fix, still deadlocks (everyone
        #    materializes simultaneously and no one commits to moving first) — same symptom as the
        #    Town03 roundabout gridlock, just as zero throttle instead of visible yielding, and not
        #    fixed by --ignore-vehicles-pct alone here. Staggering each spawn by SPAWN_STAGGER_S
        #    resolved it cleanly (22/24 moving within a few seconds vs 0/20 spawned all at once).
        new_ids = []
        for sp in points:
            bp = random.choice(car_bps)
            if bp.has_attribute("color"):
                bp.set_attribute("color", random.choice(bp.get_attribute("color").recommended_values))
            bp.set_attribute("role_name", "autopilot")
            v = world.try_spawn_actor(bp, sp)
            if v is not None:
                v.set_autopilot(True, tm.get_port())
                if args.ignore_vehicles_pct > 0:
                    tm.ignore_vehicles_percentage(v, args.ignore_vehicles_pct)
                new_ids.append(v.id)
            time.sleep(SPAWN_STAGGER_S)
        return len(new_ids)

    def one_pass() -> None:
        vs = list(world.get_actors().filter("vehicle.*"))
        inside, remove = [], []
        for v in vs:
            loc = v.get_location()
            bad_type = not args.allow_buses and v.type_id in BUS_BLUEPRINTS
            keep = (loc.x - cx) ** 2 + (loc.y - cy) ** 2 <= args.radius ** 2 and not bad_type
            (inside if keep else remove).append(v)
        # a bus is always removed (even with --keep-outside), since it stalls traffic where it stands
        to_destroy = remove if not args.keep_outside else [v for v in remove if v.type_id in BUS_BLUEPRINTS]
        if to_destroy:
            client.apply_batch([carla.command.DestroyActor(v.id) for v in to_destroy])
            time.sleep(0.3)
        need = min(max(0, args.vehicles - len(inside)), args.max_spawn_per_pass)
        spawned = spawn_at((near_points * (need // len(near_points) + 1))[:need]) if need else 0
        print(f"[focus] kept {len(inside)}, removed {len(to_destroy)} (outside radius or a bus), "
              f"spawned {spawned} -> ~{len(inside) + spawned} near the center")

    one_pass()
    if not args.watch:
        return
    print(f"[focus] --watch on: recycling every {args.interval}s, Ctrl+C to stop")
    try:
        while True:
            time.sleep(args.interval)
            one_pass()
    except KeyboardInterrupt:
        print("\n[focus] stopped")


if __name__ == "__main__":
    main()
