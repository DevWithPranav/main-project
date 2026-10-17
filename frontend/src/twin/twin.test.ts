// @vitest-environment node
// (moved from twin/src: pure twin modules, run without a DOM as before)
import { describe, expect, it } from "vitest";
import { carlaToEnu, enuToCarla, parseAnchor } from "./coords";
import { junctionMeshes, LIFT } from "./laneGeometry";
import { countsText, liveTime, signalColor, SignalStore } from "./signals";
import { headColour, phaseAt, plannedTimeline, planSignals, stateText } from "./planSignals";
import { gridHotspots, LiveTrails, pinStyle, problemSections } from "./layers";
import { applyOverrides, buildProfile, globToRegExp, overrideTarget, validateOverride, validateZone } from "./planEdits";
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
  it("accepts every road attribute and previews them", () => {
    expect(validateOverride({ lane_id: "r5_s0_l-1", road_class: "highway", lane_change: "none", bridge: true, ramp: "on" }, lanes)).toEqual([]);
    expect(validateOverride({ lane_id: "r5_s0_l-1", road_class: "motorway" as never }, lanes)).toHaveLength(1);
    expect(validateOverride({ lane_id: "r5_s0_l-1", lane_type: "shoulder", restricted: "bus" }, lanes)).toContain("only driving lanes can be restricted");
    expect(validateOverride({ lane_id: "r5_s0_l-1" }, lanes)).toContain("nothing to change");
    const out = applyOverrides(lanes, [{ lane_id: "r5_s0_l-1", road_class: "highway", one_way: true }]);
    expect(out[0]).toMatchObject({ road_class: "highway", one_way: true });
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

describe("junctions", () => {
  it("covers the junction with one surface fanned from the centre, an outline and the stop lines", () => {
    const square: [number, number][] = [[10, 10], [-10, 10], [-10, -10], [10, -10]];
    const j = { id: "J1", centre: [0, 0] as [number, number], polygon: square,
      approaches: [{ id: "J1_a0", road_id: "1", lanes: ["l"], x: -10, y: 0, heading_deg: 0, stop_line: [[-10, -1.75], [-10, 1.75]] as [number, number][] }] };
    const m = junctionMeshes(j, 2);
    expect(m.surface.positions.length / 3).toBe(5); // the centre + the 4 outline points
    expect(m.surface.indices).toHaveLength(12); // 4 triangles, closing back to the first point
    expect(m.surface.positions.filter((_, i) => i % 3 === 2).every((z) => z === 2 + LIFT.junction)).toBe(true);
    expect(m.edge.indices.length).toBe(4 * 6); // a strip along the closed ring
    expect(m.stops.positions.length / 3).toBe(4); // one quad across the approach
    const xs = m.stops.positions.filter((_, i) => i % 3 === 0);
    expect(Math.max(...xs) - Math.min(...xs)).toBeCloseTo(0.4); // the stop line is 0.4 m wide, along the road
  });
});

describe("CARLA traffic lights", () => {
  const lights = [{ id: "7", stop_lines: [{ id: "tl_7_0", line: [[0, -1.75], [0, 1.75]] as [number, number][] }] },
    { id: "8", stop_lines: [{ id: "tl_8_0", line: [[40, -1.75], [40, 1.75]] as [number, number][] }] }];
  it("replays each light's state at the replay time", () => {
    const s = SignalStore.fromSession({ lights, timelines: { "7": [[1, "Green"], [5, "Yellow"], [7, "Red"]] }, time_base: "time_s" })!;
    expect(s.stateAt("7", 0.5)).toBeNull();
    expect(s.stateAt("7", 1)).toBe("Green");
    expect(s.stateAt("7", 6.9)).toBe("Yellow");
    expect(s.stateAt("7", 100)).toBe("Red");
    expect(s.stateAt("7", null)).toBe("Red");
    expect(s.countsAt(6)).toEqual({ Yellow: 1, none: 1 });
    expect(countsText(2, s.countsAt(6))).toBe("2 lights · 1 yellow");
    expect(SignalStore.fromSession({ lights: [], timelines: {}, time_base: null })).toBeNull();
  });
  it("live: out-of-order changes, the vehicles' sim time, bounded history", () => {
    const s = new SignalStore();
    s.setLights(lights);
    s.add(10, "7", "Green");
    s.add(30, "7", "Red");
    s.add(20, "7", "Yellow"); // arrived late
    s.add(20, "7", "Yellow"); // twice
    expect(s.stateAt("7", 25)).toBe("Yellow");
    expect(s.latestT).toBe(30);
    // the cars on screen are at sim time 29 (detector delay): the light is still yellow for them
    expect(s.stateAt("7", liveTime([{ t_s: 28.5 }, { t_s: 29 }, {}]))).toBe("Yellow");
    expect(liveTime([])).toBeNull();
    s.add(1000, "7", "Green");
    s.trim(600);
    expect(s.stateAt("7", 399)).toBe("Red"); // the state before the kept window survives
    expect(s.stateAt("7", 15)).toBeNull(); // older history is gone: unknown, not a wrong colour
    expect(s.lightsNear(39, 0, 5).map((l) => l.id)).toEqual(["8"]);
    expect(signalColor("Red")).toBe("#fa5252");
    expect(signalColor("Off")).toBe(signalColor(null));
  });
});

describe("plan signals (scenario builder)", () => {
  const lane = (id: string, c: [number, number][], junction = false) => ({ id, centreline: c, width_m: 3.5, lane_type: "driving", junction, junction_id: junction ? "1" : null });
  const scene = {
    scene: "T",
    lanes: [lane("x_in", [[-300, 0], [-10, 0]]), lane("jx", [[-10, 0], [10, 0]], true), lane("y_in", [[0, -300], [0, -10]]), lane("jy", [[0, -10], [0, 10]], true)],
    junctions: [{ id: "J1", centre: [0, 0] as [number, number], polygon: [], approaches: [
      { id: "J1_a0", road_id: "2", lanes: ["y_in"], x: 0, y: -10, heading_deg: 90, stop_line: [[-1.75, -10], [1.75, -10]] as [number, number][] },
      { id: "J1_a1", road_id: "1", lanes: ["x_in"], x: -10, y: 0, heading_deg: 0, stop_line: [[-10, -1.75], [-10, 1.75]] as [number, number][] }] }],
  };
  it("places a junction signal as one controller, phased by road axis, 1 m before the junction edge", () => {
    const items = [0, 1].map((k) => ({ id: `s${k}`, kind: "traffic_signal", x: 0, y: 0, params: { cycle_s: 40, green_s: 14 }, target: { junction_id: "J1", approach_id: `J1_a${k}` } }));
    const [ps] = planSignals(items, scene);
    expect(ps.junction).toBe("J1");
    expect(ps.heads.map((h) => [Math.round(h.x), Math.round(h.y), h.group])).toEqual([[0, -11, "A"], [-11, 0, "B"]]);
    expect(Math.abs(ps.heads[1].bar[0][1] - ps.heads[1].bar[1][1])).toBeCloseTo(3.5); // across the eastbound lane
  });
  it("a signal on a road controls the lanes within reach, 3 m before it", () => {
    const [ps] = planSignals([{ id: "p", kind: "traffic_signal", x: -50, y: 0 }], scene);
    expect(ps.heads).toHaveLength(1);
    expect(Math.round(ps.heads[0].x)).toBe(-53);
    expect(planSignals([{ id: "far", kind: "traffic_signal", x: -50, y: 80 }], scene)).toEqual([]);
  });
  it("planned timing: A green, clearance (yellow for A), B green, clearance", () => {
    const { tl, period } = plannedTimeline(40, 14); // B = 40 - 14 - 4 = 22
    expect(period).toBe(40);
    const at = (t: number) => phaseAt(tl, t, period);
    expect(at(5)).toMatchObject({ phase: "A", left: 9 });
    expect(headColour("traffic_signal", "A", at(15).phase, at(15).prev)).toBe("yellow");
    expect(headColour("traffic_signal", "B", at(15).phase, at(15).prev)).toBe("red");
    expect(at(20).phase).toBe("B");
    expect(at(39).phase).toBe("R");
    expect(headColour("traffic_signal", "B", at(39).phase, at(39).prev)).toBe("yellow");
    expect(at(45)).toMatchObject({ phase: "A", left: 9 }); // looped
    expect(stateText("traffic_signal", at(5).phase, at(5).prev, at(5).left, new Set(["A", "B"]))).toBe("A green 9 s · B red");
    expect(stateText("stop_sign", "ALL", null, null, new Set(["A"]))).toBe("stop");
  });
  it("simulated timeline (no loop): the state at t and the time to the next change", () => {
    const tl: [number, string][] = [[0, "A"], [10.2, "R"], [12.2, "B"]];
    expect(phaseAt(tl, 11)).toEqual({ phase: "R", prev: "A", left: expect.closeTo(1.2, 5) });
    expect(phaseAt(tl, 30)).toEqual({ phase: "B", prev: "R", left: null });
  });
});
