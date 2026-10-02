"""Dense, smooth traffic around a point that keeps itself unblocked - run it for the whole recording.

focus_traffic.py spawns once (or recycles everything outside the radius in --watch mode) and then
traffic slowly seizes up: a queue the Traffic Manager cannot resolve stays for ever (seen on the Town03
roundabout, Town04, Town01). This script instead runs a light loop every --cycle seconds that

  1. finds BLOCKERS and destroys them:
       - the head of a stopped chain that is not waiting at a red/yellow light (it is not being held
         by anything, so it is stuck: green light and not moving, or no light at all),
       - any vehicle stopped longer than --deadlock-s that is not at a red light (a junction
         gridlock has no head, so the longest-stopped cars are removed instead),
       - vehicles that have drifted outside the radius (recycled, so the traffic stays concentrated),
  2. refills up to --vehicles with a few cars per cycle (not a big batch), only at spawn points that are
     clear of other cars and **out of the drone's view** (--no-pop-radius), so cars neither appear nor
     vanish on screen,
  3. gives every car its own speed offset and following gap, so traffic is not a rigid convoy.

It also **hosts the Traffic Manager** (the TM lives in the process that created it: when that process
exits every autopilot car stops), so it replaces the old tm_keeper / focus_traffic.py combination. Keep
it running during the flight; Ctrl+C stops it and the cars freeze where they are.

Run with CarlaAir up and the map already loaded (after load_map.py), before recording:
    python traffic_flow.py --center -40 133 --radius 140 --vehicles 140
Cars already in the world are destroyed first (--keep-existing adopts them). Buses are left out (--allow-buses to include them).
"""

import argparse
import math
import random
import time

import carla

BUS_BLUEPRINTS = {"vehicle.mitsubishi.fusorosa"}
STOPPED_MPS = 0.3
RED_OR_YELLOW = (carla.TrafficLightState.Red, carla.TrafficLightState.Yellow)


def lane_center_points(m, cx, cy, radius, spacing=10.0):
    out = []
    for wp in m.generate_waypoints(spacing):
        if wp.is_junction or wp.lane_type != carla.LaneType.Driving:
            continue
        loc = wp.transform.location
        if (loc.x - cx) ** 2 + (loc.y - cy) ** 2 <= radius ** 2:
            tf = wp.transform
            out.append(carla.Transform(carla.Location(loc.x, loc.y, loc.z + 0.3), tf.rotation))
    return out


def dist2(a, b):
    return (a.x - b.x) ** 2 + (a.y - b.y) ** 2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--center", type=float, nargs=2, metavar=("X", "Y"), required=True)
    ap.add_argument("--radius", type=float, default=140.0, help="Metres from --center to keep traffic within")
    ap.add_argument("--vehicles", type=int, default=120, help="Target vehicle count inside the radius")
    ap.add_argument("--tm-port", type=int, default=8000)
    ap.add_argument("--cycle", type=float, default=2.5, help="Seconds between maintenance passes")
    ap.add_argument("--stall-s", type=float, default=30.0,
                    help="A chain head stopped this long (not at a red/yellow light) is a blocker")
    ap.add_argument("--deadlock-s", type=float, default=90.0,
                    help="Any vehicle stopped this long (not at a red light) is removed, headless gridlock too")
    ap.add_argument("--no-pop-radius", type=float, default=70.0,
                    help="Don't spawn closer than this (m, horizontal) to the drone, so cars don't appear in view")
    ap.add_argument("--spawn-clear", type=float, default=12.0, help="Spawn only if no car is within this many m")
    ap.add_argument("--max-spawn", type=int, default=6, help="Max spawns per cycle while filling up")
    ap.add_argument("--max-spawn-steady", type=int, default=2, help="Max spawns per cycle once near the target")
    ap.add_argument("--max-destroy", type=int, default=5, help="Max destroys per cycle (keeps AirSim responsive)")
    ap.add_argument("--min-gap", type=float, default=3.5, help="TM following distance (m); per car +-1.5")
    ap.add_argument("--ignore-vehicles-pct", type=float, default=30.0,
                    help="Chance (0-100) a car ignores other cars when deciding to go; keeps junctions flowing")
    ap.add_argument("--allow-buses", action="store_true")
    ap.add_argument("--keep-existing", action="store_true",
                    help="Adopt vehicles already in the world instead of destroying them at start. Off by "
                         "default: re-registering cars that belonged to a dead Traffic Manager aborts the "
                         "process inside set_autopilot (native crash, seen 2026-10-01)")
    args = ap.parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(args.timeout)
    world = client.get_world()
    m = world.get_map()
    cx, cy = args.center
    centre = carla.Location(cx, cy, 0.0)
    print(f"[flow] map {m.name}, centre ({cx:.0f}, {cy:.0f}), radius {args.radius:.0f} m, target {args.vehicles}")

    tm = client.get_trafficmanager(args.tm_port)
    tm.set_global_distance_to_leading_vehicle(args.min_gap)
    tm.global_percentage_speed_difference(15.0)

    points = [sp for sp in m.get_spawn_points() if dist2(sp.location, centre) <= args.radius ** 2]
    if len(points) < 15:
        points += lane_center_points(m, cx, cy, args.radius)
    if not points:
        raise SystemExit("No spawn points near the centre - increase --radius.")
    print(f"[flow] {len(points)} spawn points in the radius")

    bps = [bp for bp in world.get_blueprint_library().filter("vehicle.*")
           if int(bp.get_attribute("number_of_wheels")) == 4]
    if not args.allow_buses:
        bps = [bp for bp in bps if bp.id not in BUS_BLUEPRINTS]

    if not args.keep_existing:
        old = list(world.get_actors().filter("vehicle.*"))
        if old:
            client.apply_batch([carla.command.DestroyActor(v.id) for v in old])
            print(f"[flow] cleared {len(old)} existing vehicles (use --keep-existing to adopt them)")
            time.sleep(2.0)

    def adopt(v):
        v.set_autopilot(True, tm.get_port())
        if args.ignore_vehicles_pct > 0:
            tm.ignore_vehicles_percentage(v, args.ignore_vehicles_pct)
        tm.vehicle_percentage_speed_difference(v, random.uniform(0.0, 30.0))
        tm.distance_to_leading_vehicle(v, max(1.5, args.min_gap + random.uniform(-1.5, 1.5)))

    known: set[int] = set()
    stopped_since: dict[int, float] = {}
    removed_blockers = removed_stray = spawned = 0
    cycle = 0
    print("[flow] running - Ctrl+C to stop (cars freeze when this exits)")
    try:
        while True:
            t0 = time.time()
            vs = list(world.get_actors().filter("vehicle.*"))
            for v in vs:
                if v.id not in known:
                    adopt(v)
                    known.add(v.id)
            known &= {v.id for v in vs}

            drone = next(iter(world.get_actors().filter("airsim.*")), None)
            dloc = drone.get_location() if drone else None

            now = time.time()
            info = {}
            for v in vs:
                loc, vel = v.get_location(), v.get_velocity()
                moving = math.sqrt(vel.x ** 2 + vel.y ** 2) > STOPPED_MPS
                if moving:
                    stopped_since.pop(v.id, None)
                else:
                    stopped_since.setdefault(v.id, now)
                info[v.id] = (v, loc, v.get_transform().get_forward_vector(), moving)
            for vid in [i for i in stopped_since if i not in info]:
                stopped_since.pop(vid)

            def in_view(loc):
                return dloc is not None and dist2(loc, dloc) < args.no_pop_radius ** 2

            doomed = []  # (id, reason)
            stopped = [i for i in info if not info[i][3]]
            for vid in stopped:
                v, loc, fwd, _ = info[vid]
                age = now - stopped_since.get(vid, now)
                if age < args.stall_s:
                    continue
                at_red = False
                if v.is_at_traffic_light():
                    at_red = v.get_traffic_light_state() in RED_OR_YELLOW
                if at_red:
                    continue
                ahead_stopped = False
                for oid in stopped:
                    if oid == vid:
                        continue
                    d = info[oid][1]
                    dx, dy = d.x - loc.x, d.y - loc.y
                    along = dx * fwd.x + dy * fwd.y
                    lateral = abs(dx * fwd.y - dy * fwd.x)
                    if 0.0 < along < 14.0 and lateral < 2.4:
                        ahead_stopped = True
                        break
                if not ahead_stopped:
                    doomed.append((vid, "blocker"))
                elif age >= args.deadlock_s:
                    doomed.append((vid, "deadlock"))
            for vid, (v, loc, _, _) in info.items():
                if dist2(loc, centre) > (args.radius * 1.15) ** 2 and not in_view(loc):
                    doomed.append((vid, "stray"))

            # a blocker in view is removed too, but strays only out of view (handled above); keep the rate low
            doomed = list({vid: r for vid, r in doomed}.items())[:args.max_destroy]
            if doomed:
                client.apply_batch([carla.command.DestroyActor(vid) for vid, _ in doomed])
                for vid, why in doomed:
                    removed_blockers += why != "stray"
                    removed_stray += why == "stray"
                    stopped_since.pop(vid, None)
                    known.discard(vid)
                    info.pop(vid, None)

            inside = [i for i in info if dist2(info[i][1], centre) <= args.radius ** 2]
            deficit = args.vehicles - len(inside)
            budget = min(deficit, args.max_spawn if len(inside) < 0.85 * args.vehicles else args.max_spawn_steady)
            if budget > 0:
                occupied = [info[i][1] for i in info]
                random.shuffle(points)
                for sp in points:
                    if budget <= 0:
                        break
                    if in_view(sp.location):
                        continue
                    if any(dist2(sp.location, o) < args.spawn_clear ** 2 for o in occupied):
                        continue
                    bp = random.choice(bps)
                    if bp.has_attribute("color"):
                        bp.set_attribute("color", random.choice(bp.get_attribute("color").recommended_values))
                    bp.set_attribute("role_name", "autopilot")
                    v = world.try_spawn_actor(bp, sp)
                    if v is None:
                        continue
                    adopt(v)
                    known.add(v.id)
                    occupied.append(sp.location)
                    spawned += 1
                    budget -= 1
                    time.sleep(0.4)  # staggered: simultaneous spawns deadlock (see focus_traffic.py)

            cycle += 1
            if cycle % 8 == 1:
                moving_n = sum(1 for i in inside if info[i][3])
                print(f"[flow] {len(inside)} in radius ({moving_n} moving) | total spawned {spawned}, "
                      f"blockers removed {removed_blockers}, strays recycled {removed_stray}", flush=True)
            time.sleep(max(0.0, args.cycle - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\n[flow] stopped")


if __name__ == "__main__":
    main()

