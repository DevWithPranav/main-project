"""Force-load a map at runtime via the CARLA API.

Found necessary (2026-09-26): this CarlaAir packaged build ignores the map
name passed to StartCarlaAir.bat / CarlaUE4.exe — it always boots to
Town10HD regardless of what was requested, even though the launcher prints
the requested name. `client.load_world()` does work and actually switches
the map; this script is the fix until the launcher itself is patched.

IMPORTANT: this resets the world, destroying every actor already spawned —
including auto_traffic.py's vehicles/walkers and any drone actor. Run this
FIRST, right after "CarlaAir is ready.", before spawning traffic or taking
off. If you started with --traffic-vehicles > 0 (the default), that traffic
just got destroyed by the reload — spawn it again afterward (see below).

Also found (2026-09-29): after `load_world()`, AirSim's own RPC server can stay
unresponsive for 15-25s while it resyncs to the new map — a fresh client fails
even a bare ping in that window, not just a stale one, and it looks identical to
a genuine hang. It always recovers on its own; this script now waits for it
before returning, so anything started after load_map.py (fly_drone.py,
record_flight.py) doesn't hit that window. Don't "fix" an apparent freeze here
by restarting CarlaAir — wait it out first.

Usage:
    python load_map.py Town03
    python load_map.py Town03 --vehicles 80   # also (re)spawn traffic after loading
    python load_map.py Town03 --vehicles 80 --weather CloudySunset   # and set the weather
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

import carla


def wait_for_airsim(host: str, port: int, timeout: float = 40.0) -> None:
    try:
        import airsim
    except ImportError:
        return  # not this env's job to have airsim; caller's own client will surface any real problem
    print("[load_map] waiting for AirSim to resync with the new map...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            c = airsim.MultirotorClient(port=port, timeout_value=3)
            c.confirmConnection()
            c.ping()
            print(f"[load_map] AirSim ready ({time.time() - start:.0f}s)")
            return
        except Exception:
            time.sleep(1)
    print(f"[load_map] AirSim still not responding after {timeout:.0f}s — this has always recovered on its "
          f"own within ~25s before, so it may just need longer; check again before assuming it's really stuck")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("map", help="Map name, e.g. Town03, Town10HD, Town03_Opt")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--vehicles", type=int, default=0, help="Re-spawn this many vehicles via auto_traffic.py after loading")
    ap.add_argument("--walkers", type=int, default=0)
    ap.add_argument("--weather", default=None,
                    help="Also set the weather (same presets as record_flight.py --weather: ClearNoon, "
                         "CloudySunset, WetNoon, MidRainyNoon, Fog, Night, ...)")
    ap.add_argument("--sun-altitude", type=float, default=None)
    ap.add_argument("--airsim-port", type=int, default=41451)
    ap.add_argument("--no-wait-airsim", action="store_true",
                    help="Don't wait for AirSim to resync after the switch (only useful if this run doesn't "
                         "involve the drone at all)")
    args = ap.parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(args.timeout)

    before = client.get_world().get_map().name
    print(f"[load_map] currently loaded: {before}")
    if before.rsplit("/", 1)[-1] == args.map:
        print(f"[load_map] already on {args.map}, nothing to do")
    else:
        print(f"[load_map] loading {args.map} ... (this destroys all current actors)")
        client.load_world(args.map)
        time.sleep(3)
        after = client.get_world().get_map().name
        print(f"[load_map] now loaded: {after}")
        if after.rsplit("/", 1)[-1] != args.map:
            raise SystemExit(f"Map did not switch to {args.map} — check the name is one of this "
                              f"build's maps (Town01-05, Town10HD, and _Opt variants).")
        if not args.no_wait_airsim:
            wait_for_airsim(args.host, args.airsim_port)

    if args.weather or args.sun_altitude is not None:
        from record_flight import apply_weather
        w = apply_weather(client.get_world(), args.weather, args.sun_altitude)
        print(f"[load_map] weather {args.weather or ''}: sun {w['sun_altitude_angle']} deg, fog {w['fog_density']}, "
              f"rain {w['precipitation']}")

    if args.vehicles or args.walkers:
        auto_traffic = Path(__file__).resolve().parents[2] / "CarlaAir-v0.1.7-Windows11-x86_64" / "auto_traffic.py"
        if not auto_traffic.exists():
            print(f"[load_map] auto_traffic.py not found at {auto_traffic}, spawn traffic manually")
            return
        print(f"[load_map] spawning traffic: {args.vehicles} vehicles, {args.walkers} walkers")
        subprocess.Popen([sys.executable, str(auto_traffic), "--vehicles", str(args.vehicles),
                          "--walkers", str(args.walkers), "--port", str(args.port)])
        print("[load_map] traffic process started in the background")


if __name__ == "__main__":
    main()
