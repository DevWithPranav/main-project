"""Generate SUMO routes (passenger cars only) for a CARLA map's SUMO net.

Usage (carlaAir env, SUMO_HOME set to its site-packages\\sumo):
    python make_routes.py Town10HD --end 900 --period 0.6

Reads  <map>/<map>.net.xml, writes <map>/<map>.rou.xml and <map>/<map>.sumocfg. The sumocfg pulls in
CARLA's carlavtypes.rou.xml from the Co-Simulation/Sumo example folder (vehicle types the bridge maps
to CARLA blueprints), and every trip is assigned one of the passenger-car types in it.
"""

import argparse
import os
import random
import subprocess
import sys
from pathlib import Path

import lxml.etree as ET

HERE = Path(__file__).resolve().parent
SUMO_EXAMPLES = Path(r"D:\Main-Project\project data\WindowsNoEditor\Co-Simulation\Sumo\examples")
CARS = [
    "vehicle.audi.a2", "vehicle.audi.tt", "vehicle.jeep.wrangler_rubicon", "vehicle.chevrolet.impala",
    "vehicle.mini.cooper_s", "vehicle.mercedes.coupe", "vehicle.bmw.grandtourer", "vehicle.citroen.c3",
    "vehicle.ford.mustang", "vehicle.volkswagen.t2", "vehicle.lincoln.mkz_2017", "vehicle.seat.leon",
    "vehicle.nissan.patrol", "vehicle.nissan.micra",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("--end", type=float, default=900.0, help="Seconds over which vehicles are departed")
    ap.add_argument("--period", type=float, default=0.6, help="Seconds between departures (lower = denser)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    d = HERE / args.map
    net = d / f"{args.map}.net.xml"
    sumo_home = Path(os.environ["SUMO_HOME"])
    trips, routes = d / "trips.tmp.xml", d / f"{args.map}.rou.tmp.xml"
    subprocess.run([sys.executable, str(sumo_home / "tools" / "randomTrips.py"), "-n", str(net),
                    "-o", str(trips), "-r", str(routes), "-e", str(args.end), "-p", str(args.period),
                    "--seed", str(args.seed), "--validate", "--min-distance", "150",
                    "--vehicle-class", "passenger", "--lanes", "--fringe-factor", "5"], check=True)

    rng = random.Random(args.seed)
    tree = ET.parse(str(routes))
    n = 0
    for veh in tree.getroot().iter("vehicle"):
        veh.set("type", rng.choice(CARS))
        veh.set("departLane", "best")
        veh.set("departSpeed", "max")
        n += 1
    tree.write(str(d / f"{args.map}.rou.xml"), pretty_print=True, xml_declaration=True, encoding="UTF-8")
    trips.unlink(missing_ok=True)
    routes.unlink(missing_ok=True)

    (d / f"{args.map}.sumocfg").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<configuration>\n    <input>\n'
        f'        <net-file value="{args.map}.net.xml"/>\n'
        f'        <route-files value="{(SUMO_EXAMPLES / "carlavtypes.rou.xml").as_posix()},{args.map}.rou.xml"/>\n'
        '    </input>\n</configuration>\n')
    print(f"wrote {n} vehicles and {args.map}.sumocfg in {d}")


if __name__ == "__main__":
    main()
