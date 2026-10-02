"""Manual drone flight on its own — keyboard and gamepad at the same time — with no
recording. Run it once CarlaAir is up and leave it running; start and stop
recordings separately with `record_flight.py --no-control`. Keeping the controls in
their own process means the recorder's camera callbacks can't delay flight commands.

Controls (same as record_flight.py):
    W/S  forward/back   A/D  left/right   Space/Shift  up/down   Q/E  yaw
    T    take off       L    land
    Gamepad: L-stick move, R-stick yaw, RT climb, LT descend, LB slow, RB fast,
             A take off, B land
Esc and the gamepad's Start button are left to the recorder. Quit with Ctrl+C in this
window: the drone keeps hovering where it is.

Run in the carlaAir conda env, with CarlaAir already running:
    python fly_drone.py
    python fly_drone.py --speed 8 --no-gamepad
"""

import argparse
import time

import airsim

from record_flight import KeyboardControl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--airsim-port", type=int, default=41451)
    ap.add_argument("--speed", type=float, default=5.0, help="m/s at full stick / key")
    ap.add_argument("--yaw-rate", type=float, default=45.0, help="deg/s at full stick / key")
    ap.add_argument("--rpc-timeout", type=float, default=5.0)
    ap.add_argument("--no-gamepad", action="store_true", help="Keyboard only; don't read a game controller")
    args = ap.parse_args()

    for attempt in range(30):  # CarlaAir may still be starting when this is launched with it
        try:
            client = airsim.MultirotorClient(port=args.airsim_port, timeout_value=args.rpc_timeout)
            client.confirmConnection()
            break
        except Exception:
            print(f"  waiting for AirSim ({attempt + 1}/30) ...")
            time.sleep(2)
    else:
        raise SystemExit("AirSim not reachable — is CarlaAir running?")
    client.enableApiControl(True)
    client.armDisarm(True)

    kbd = KeyboardControl(args.airsim_port, speed=args.speed, yaw_rate=args.yaw_rate, rpc_timeout=args.rpc_timeout,
                          use_gamepad=not args.no_gamepad, allow_quit=False)
    kbd.start()
    print("=" * 50)
    print("  Drone controls (keyboard + gamepad) — no recording here")
    print("  W/S/A/D move, Space/Shift up/down, Q/E yaw, T takeoff, L land")
    if not args.no_gamepad:
        print("  Gamepad: L-stick move, R-stick yaw, RT up, LT down, LB slow, RB fast, A takeoff, B land")
    print("  Record from another prompt: python record_flight.py --vehicles 0 --labels --no-control")
    print("  Ctrl+C here to quit (the drone keeps hovering)")
    print("=" * 50)
    try:
        kbd.control_loop()  # main thread: pygame is initialised and read on this one thread
    except KeyboardInterrupt:
        pass
    finally:
        kbd.running = False
        kbd.stop()
        try:
            client.hoverAsync()
        except Exception:
            pass
        print("\n[fly] stopped — drone left hovering")


if __name__ == "__main__":
    main()
