"""Put the drone at a CARLA world spot and hold it there (for the staging session).

AirSim flies in its own frame (NED, origin at the drone's start). Measured on this CarlaAir
build (2026-10-10): its axes line up with CARLA's, carla = origin + (x, y, -z), and the AirSim
yaw equals the CARLA yaw. The origin is read live (drone actor's CARLA location minus its AirSim
position), so it stays right after a map load.

Usage (venv_sim, CarlaAir running, map already loaded):
    python simulation/carla_scripts/goto_spot.py -30.1 111.2 --yaw 0
"""

import argparse
import math
import time

import airsim
import carla

from record_flight import find_drone_actor


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("x", type=float, help="CARLA world x (m)")
    ap.add_argument("y", type=float, help="CARLA world y (m)")
    ap.add_argument("--yaw", type=float, default=0.0, help="Heading in degrees (0 = +x, 90 = +y)")
    ap.add_argument("--altitude", type=float, default=67.6, help="Metres above the road at the spot")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--airsim-port", type=int, default=41451)
    args = ap.parse_args()

    world = carla.Client(args.host, args.port).get_world()
    drone = None
    for _ in range(20):
        drone = find_drone_actor(world)
        if drone is not None:
            break
        time.sleep(0.5)
    if drone is None:
        raise SystemExit("Drone actor not found in CARLA. Is CarlaAir running with AirSim enabled?")

    client = airsim.MultirotorClient(port=args.airsim_port)
    client.confirmConnection()
    client.enableApiControl(True)
    client.armDisarm(True)

    p = client.simGetVehiclePose().position
    loc = drone.get_transform().location
    ox, oy, oz = loc.x - p.x_val, loc.y - p.y_val, loc.z + p.z_val

    wp = world.get_map().get_waypoint(carla.Location(args.x, args.y, 0.0), project_to_road=True)
    ground = wp.transform.location.z if wp is not None else 0.0
    nx, ny, nz = args.x - ox, args.y - oy, -(ground + args.altitude - oz)
    yaw = math.radians(args.yaw)
    print(f"[goto] CARLA ({args.x}, {args.y}) road z {ground:.1f} m -> AirSim ({nx:.1f}, {ny:.1f}, {nz:.1f})")

    if client.getMultirotorState().landed_state == airsim.LandedState.Landed:
        client.takeoffAsync(timeout_sec=10).join()
    client.simSetVehiclePose(airsim.Pose(airsim.Vector3r(nx, ny, nz), airsim.to_quaternion(0, 0, yaw)), True)

    # A single hoverAsync() doesn't hold on this build (tested 2026-10-10: the drone sank to the
    # ground within 10 s), so keep re-sending the spot until Ctrl+C.
    print("[goto] holding the spot; keep this window open, Ctrl+C when the spot is done")
    last = 0.0
    try:
        while True:
            client.moveToPositionAsync(nx, ny, nz, 2.0, timeout_sec=3,
                                       yaw_mode=airsim.YawMode(False, args.yaw)).join()
            if time.time() - last > 10.0:
                last = time.time()
                t = drone.get_transform()
                err = math.hypot(t.location.x - args.x, t.location.y - args.y)
                print(f"[goto] drone at ({t.location.x:.1f}, {t.location.y:.1f}), "
                      f"{t.location.z - ground:.1f} m above the road, yaw {t.rotation.yaw:.1f} deg "
                      f"({err:.1f} m from the spot)", flush=True)
    except KeyboardInterrupt:
        print("\n[goto] stopped holding (the drone will sink); run again for the next spot")


if __name__ == "__main__":
    main()
