import { describe, expect, it } from "vitest";
import { carlaToEnu, enuToCarla, parseAnchor } from "./coords";
import { gridHotspots, LiveTrails, pinStyle, problemSections } from "./layers";
import { buildProfile, globToRegExp, overrideTarget, validateOverride, validateZone } from "./planEdits";
import { indexEventsByTrack, parseTrajectories, sampleTrack, stateAt, statesAt, timeRange } from "./replay";
import type { Lane, TwinEvent } from "./types";

const ev = (o: Partial<TwinEvent>): TwinEvent => ({ event_id: "e", type: "speeding", x: 0, y: 0, ...o });

describe("replay", () => {
  const tracks = parseTrajectories({ "7": [[10, 0, 0, 36], [11, 10, 0, 36]], bad: [[NaN, 0, 0, null]] });
  it("parses, ranges and interpolates", () => {
    expect(tracks).toHaveLength(1);
    expect(timeRange(tracks)).toEqual([10, 11]);
    const s = sampleTrack(tracks[0], 10.5)!;
    expect(s.x).toBeCloseTo(5);
    expect(s.heading_deg).toBeCloseTo(0);
    expect(sampleTrack(tracks[0], 12)).toBeNull();
  });
  it("colours a vehicle checking then flagged, anomalies never", () => {
    const idx = indexEventsByTrack([ev({ track_ids: [7], start_s: 10, flag_s: 10.6 }), ev({ kind: "anomaly", type: "pothole", t_s: 10 })]);
    expect(stateAt("7", 10.2, idx)).toBe("checking");
    expect(stateAt("7", 10.8, idx)).toBe("flagged");
    expect(statesAt(tracks, 10.8, idx)[0].state).toBe("flagged");
  });
});

describe("layers", () => {
  const events = [
    ev({ event_id: "a", x: 1, y: 1, lane_id: "r1_s0_l-1" }),
    ev({ event_id: "b", x: 3, y: 2, lane_id: "r1_s0_l-1", type: "wrong_way" }),
    ev({ event_id: "c", x: 60, y: 60, kind: "anomaly", type: "pothole", lane_id: "r2_s0_l1" }),
  ];
  it("hotspots on the backend's 25 m grid, biggest first", () => {
    const h = gridHotspots(events);
    expect(h).toHaveLength(2);
    expect(h[0]).toMatchObject({ count: 2, x: 2, y: 1.5, by_type: { speeding: 1, wrong_way: 1 }, lane_ids: ["r1_s0_l-1"] });
  });
  it("ranks problem sections and styles anomaly pins apart", () => {
    expect(problemSections(events)[0]).toMatchObject({ lane_id: "r1_s0_l-1", count: 2 });
    expect(pinStyle(events[2]).label).toContain("pothole");
    expect(pinStyle(events[0]).label).toBeNull();
  });
  it("keeps 3 s live trails and drops vehicles that left", () => {
    const tr = new LiveTrails();
    for (let i = 0; i <= 50; i++) tr.update([{ track_id: 1, x: i, y: 0, t_s: i * 0.1 }]);
    const pts = tr.get(1);
    expect(pts.length).toBe(31); // 2.0 .. 5.0 s at 10 Hz
    expect(pts[pts.length - 1]).toEqual([50, 0]);
    tr.update([{ track_id: 2, x: 0, y: 0, t_s: 5.1 }]);
    expect(tr.get(1)).toEqual([]);
  });
});

describe("planner edits", () => {
  const lanes = [{ id: "r5_s0_l-1", centreline: [[0, 0], [10, 0]], lane_type: "driving" }] as Lane[];
  it("targets, validates and builds the profile", () => {
    expect(overrideTarget("r5_s0_l-1_p2", "lane")).toBe("r5_s0_l-1*");
    expect(globToRegExp("r5_s*_l-*").test("r5_s0_l-1")).toBe(true);
    expect(validateOverride({ lane_id: "r5_s0_l-1", speed_limit_kmh: 30 }, lanes)).toEqual([]);
    expect(validateOverride({ lane_id: "r9_s0_l-1", speed_limit_kmh: 300 }, lanes)).toHaveLength(2);
    const p = buildProfile({ profile_version: 1, name: "default", road: { lane_overrides: [{ lane_id: "x", speed_limit_kmh: 50 }] } }, "t5", [{ lane_id: "r5_s0_l-1", speed_limit_kmh: 30 }]);
    expect(p.name).toBe("t5");
    expect(p.road.lane_overrides).toHaveLength(2);
  });
  it("refuses bad zones", () => {
    expect(validateZone({ id: "z1", type: "no_parking", polygon: [[0, 0], [10, 0], [10, 10], [0, 10]] })).toEqual([]);
    expect(validateZone({ id: "z1", type: "no_parking", polygon: [[0, 0], [10, 10], [10, 0], [0, 10]] })).toContain("zone outline crosses itself");
    expect(validateZone({ id: "z1", type: "speed", polygon: [[0, 0], [1, 0]] }).length).toBeGreaterThanOrEqual(2);
  });
});

describe("coords", () => {
  it("CARLA <-> ENU flips y and parses anchors", () => {
    expect(carlaToEnu(1, 2, 3)).toEqual([1, -2, 3]);
    expect(enuToCarla(1, -2, 3)).toEqual([1, 2, 3]);
    expect(parseAnchor("10,20")).toMatchObject({ lat: 10, lon: 20 });
    expect(parseAnchor("nope")).toBeNull();
  });
});
