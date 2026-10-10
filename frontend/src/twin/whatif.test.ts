// @vitest-environment node
// (moved from twin/src: pure twin modules, run without a DOM as before)
import { describe, expect, it } from "vitest";
import { buildRequest, resultHtml, summaryText, type ScenarioRow } from "./whatif";

const zone = { id: "z1", type: "no_parking", polygon: [[0, 0], [10, 0], [10, 10]] as [number, number][] };

describe("what-if request", () => {
  it("needs a session, a name and something to simulate", () => {
    expect(buildRequest("", "x", [], [zone], null, null).error).toMatch(/session/);
    expect(buildRequest("s1", " ", [], [zone], null, null).error).toMatch(/Name/);
    expect(buildRequest("s1", "x", [], [], null, null).error).toMatch(/lane change or zone/);
  });
  it("sends overrides, zone polygons, countermeasure and area", () => {
    const { body } = buildRequest("s1", " 20 km/h ", [{ lane_id: "r24_*", speed_limit_kmh: 20 }], [zone], "speed_camera", [[0, 0], [5, 0], [5, 5]]);
    expect(body).toEqual({ session_id: "s1", name: "20 km/h", countermeasure: "speed_camera", area: [[0, 0], [5, 0], [5, 5]],
      changes: { lane_overrides: [{ lane_id: "r24_*", speed_limit_kmh: 20 }], zones: [{ type: "no_parking", polygon: zone.polygon }] } });
  });
  it("drops an area with fewer than 3 points", () => {
    expect(buildRequest("s1", "x", [], [], "speed_camera", [[0, 0], [1, 1]]).body!.area).toBeNull();
  });
});

describe("what-if result", () => {
  const done: ScenarioRow = {
    id: "scn-1", name: "zone", by: "planner", at: "2026-10-10T20:00:00Z", status: "done",
    summary: { replay_counted: [11, 16], projection_before: 2, projection_after: { events_low: 1, events_high: 1.5 } },
    result: {
      replay: { baseline: { counted: 11, by_condition: { F1: 3 } }, modified: { counted: 16, by_condition: { B3: 5, F1: 3 } },
                added: [1, 2, 3, 4, 5], removed: [], tracks: 129, near_m: 60, note: "not modelled" },
      projection: { countermeasure: "Crosswalk visibility", affected: { events: 2, by_condition: { F1: 2 } }, before: { events: 2, per_hour: 3.1 },
                    projected_after: { events_low: 1, events_high: 1.5 }, basis: "projected (rule-of-thumb)", source: { url: "https://x" } },
    },
  };
  it("summarises replay and projection apart", () => {
    expect(summaryText(done)).toBe("rules: 11 → 16 events · projected: 2 → 1–1.5");
    expect(summaryText({ ...done, status: "running" })).toBe("running…");
    expect(summaryText({ ...done, status: "failed", error: "lane override 'r9' matches no lane" })).toMatch(/matches no lane/);
  });
  it("labels the projection an estimate and the replay a measurement", () => {
    const h = resultHtml(done);
    expect(h).toContain("projected estimate");
    expect(h).toContain("measured on recorded traffic");
    expect(h).toContain("+5 / −0");
  });
  it("says not quantified when the catalogue has no factor", () => {
    const r = { ...done, result: { projection: { ...done.result!.projection, projected_after: null } } };
    expect(resultHtml(r)).toContain("not quantified");
  });
});
