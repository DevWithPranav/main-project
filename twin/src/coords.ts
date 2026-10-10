/**
 * CARLA map metres <-> local East-North-Up metres <-> Cesium world (ECEF).
 *
 * CARLA / Unreal world axes are left-handed: seen from above, +x is forward and +y points to the
 * RIGHT of +x. The twin uses a right-handed ENU frame at a configurable anchor:
 *
 *     east  =  x
 *     north = -y      (the flip turns the left-handed frame into a right-handed one)
 *     up    =  z
 *
 * so the town looks the same from above as in the CARLA spectator / the drone view (not mirrored).
 * The default anchor is lat 0 / lon 0, the geoReference CARLA writes into every town's OpenDRIVE
 * (+lat_0=0 +lon_0=0): CARLA towns carry no real location (Expected_Output Section 5.1). Real sites
 * would pass their own surveyed anchor with ?anchor=lat,lon[,h].
 *
 * Only the ECEF step uses Cesium; the rest is pure so it can be unit tested.
 */
import { Cartesian3, Matrix4, Transforms } from "cesium";

export type Vec3 = [number, number, number];

export interface Anchor {
  lat: number;
  lon: number;
  h: number;
}

export const DEFAULT_ANCHOR: Anchor = { lat: 0, lon: 0, h: 0 };

/** CARLA (x, y, z) -> local ENU (e, n, u). */
export function carlaToEnu(x: number, y: number, z = 0): Vec3 {
  return [x, -y, z];
}

/** Local ENU (e, n, u) -> CARLA (x, y, z). */
export function enuToCarla(e: number, n: number, u = 0): Vec3 {
  return [e, -n, u];
}

/** CARLA heading (degrees, atan2(dy, dx) in CARLA axes) -> compass-style ENU heading in radians
 * measured counter-clockwise from east (atan2(dn, de)). */
export function carlaHeadingToEnuRad(headingDeg: number): number {
  return (-headingDeg * Math.PI) / 180;
}

/** "lat,lon[,h]" -> Anchor (null when malformed or out of range). */
export function parseAnchor(s: string | null | undefined): Anchor | null {
  if (!s) return null;
  const p = s.split(",").map((v) => Number(v.trim()));
  if (p.length < 2 || p.some((v) => !Number.isFinite(v))) return null;
  const [lat, lon, h = 0] = p;
  if (Math.abs(lat) > 89 || Math.abs(lon) > 180) return null;
  return { lat, lon, h };
}

/** Maps CARLA metres to Cesium world coordinates through the anchor's ENU frame. */
export class Frame {
  readonly anchor: Anchor;
  readonly toWorld: Matrix4;
  readonly toLocal: Matrix4;
  private readonly scratch = new Cartesian3();

  constructor(anchor: Anchor = DEFAULT_ANCHOR) {
    this.anchor = anchor;
    const origin = Cartesian3.fromDegrees(anchor.lon, anchor.lat, anchor.h);
    this.toWorld = Transforms.eastNorthUpToFixedFrame(origin);
    this.toLocal = Matrix4.inverseTransformation(this.toWorld, new Matrix4());
  }

  /** ENU metres -> world. */
  enu(e: number, n: number, u: number, result = new Cartesian3()): Cartesian3 {
    this.scratch.x = e;
    this.scratch.y = n;
    this.scratch.z = u;
    return Matrix4.multiplyByPoint(this.toWorld, this.scratch, result);
  }

  /** CARLA metres -> world. */
  carla(x: number, y: number, z = 0, result = new Cartesian3()): Cartesian3 {
    const [e, n, u] = carlaToEnu(x, y, z);
    return this.enu(e, n, u, result);
  }

  /** World -> CARLA metres. */
  toCarla(p: Cartesian3): Vec3 {
    const l = Matrix4.multiplyByPoint(this.toLocal, p, new Cartesian3());
    return enuToCarla(l.x, l.y, l.z);
  }

  /** Flat array of ENU triples -> flat Float64Array of world xyz (for batched geometry). */
  enuArrayToWorld(enu: ArrayLike<number>): Float64Array {
    const out = new Float64Array(enu.length);
    const r = new Cartesian3();
    for (let i = 0; i < enu.length; i += 3) {
      this.enu(enu[i], enu[i + 1], enu[i + 2], r);
      out[i] = r.x;
      out[i + 1] = r.y;
      out[i + 2] = r.z;
    }
    return out;
  }
}
