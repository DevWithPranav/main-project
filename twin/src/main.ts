// 3D digital twin (Build Plan M7): the lane map in 3D (heights, bridges, markings, crosswalks),
// vehicles live (WebSocket) or replayed from a session's trajectories, violation pins, problem
// sections, and planner edits (lane limits / restricted lanes / zones) saved as a new profile version.
//
// URL: ?session=<id>&t=<sim s>&town=<Town03>&anchor=lat,lon[,h]. The dashboard can sync time with
// window.postMessage({type: "seek", t}) or ({type: "session", id}).

import "cesium/Build/Cesium/Widgets/widgets.css";
import "./style.css";
import {
  BoundingSphere,
  Cartesian2,
  Cartesian3,
  Color,
  ColorGeometryInstanceAttribute,
  ComponentDatatype,
  Geometry,
  GeometryAttribute,
  GeometryInstance,
  LabelCollection,
  LabelStyle,
  PerInstanceColorAppearance,
  PointPrimitiveCollection,
  PolylineCollection,
  Primitive,
  PrimitiveType,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  Viewer,
  Material,
} from "cesium";
import { Api, profileSummaries, unwrapProfile } from "./api";
import { Frame, parseAnchor, DEFAULT_ANCHOR } from "./coords";
import { buildLaneMeshes, crosswalkZones, HeightIndex, laneCategory, mergeInto, emptyMesh, polygonFan, type Mesh } from "./laneGeometry";
import { buildProfile, overrideTarget, validateNote, validateOverride, validateProfileName, validateZone } from "./planEdits";
import { eventTime, indexEventsByTrack, parseTrajectories, sampleTrack, statesAt, timeRange, type EventIndex, type Track } from "./replay";
import type { Lane, LaneOverride, Scene, TwinEvent, VehicleState, Zone } from "./types";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
// show runtime errors in the panel (there is no console for the people using the twin)
const showError = (msg: string) => {
  const el = document.getElementById("saveErr") ?? document.getElementById("loginErr");
  if (el) el.textContent = `Error: ${msg}`;
};
window.addEventListener("error", (e) => showError(e.message));
window.addEventListener("unhandledrejection", (e) => showError(String((e.reason as Error)?.message ?? e.reason)));
const qs = new URLSearchParams(location.search);
const api = new Api();
const frame = new Frame(parseAnchor(qs.get("anchor")) ?? DEFAULT_ANCHOR);

const STATE_COLOR: Record<string, Color> = {
  ok: Color.fromCssColorString("#40c057"),
  checking: Color.fromCssColorString("#fab005"),
  flagged: Color.fromCssColorString("#fa5252"),
};
const CATEGORY_COLOR: Record<string, string> = {
  bridge: "#7048e8", highway: "#4c6ef5", ramp: "#e64980", junction: "#868e96", shoulder: "#adb5bd",
  parking: "#ced4da", restricted: "#fd7e14", driving: "#495057",
};
const TYPE_COLOR: Record<string, string> = {
  speeding: "#fa5252", wrong_way: "#be4bdb", illegal_u_turn: "#7950f2", lane_violation: "#228be6",
  zebra_crossing: "#fd7e14", highway_stop: "#e64980", no_parking: "#fab005", red_light: "#c92a2a",
};

// ------------------------------------------------------------------ viewer

const viewer = new Viewer("viewer", {
  baseLayer: false, baseLayerPicker: false, geocoder: false, timeline: false, animation: false,
  homeButton: false, sceneModePicker: false, navigationHelpButton: false, fullscreenButton: false,
  infoBox: false, selectionIndicator: false,
});
// CARLA towns have no real terrain or imagery: hide the globe (no z-fighting with roads at z = 0)
viewer.scene.globe.show = false;
if (viewer.scene.skyAtmosphere) viewer.scene.skyAtmosphere.show = false;
if (viewer.scene.skyBox) viewer.scene.skyBox.show = false;
viewer.scene.backgroundColor = Color.fromCssColorString(matchMedia("(prefers-color-scheme: dark)").matches ? "#141517" : "#dfe5df");

const points = viewer.scene.primitives.add(new PointPrimitiveCollection());
const labels = viewer.scene.primitives.add(new LabelCollection());
const trails = viewer.scene.primitives.add(new PolylineCollection());
const pins = viewer.scene.primitives.add(new PointPrimitiveCollection());
const drawn = viewer.scene.primitives.add(new PolylineCollection());
let roadPrims: Primitive[] = [];
let lanePrim: Primitive | null = null;

function toGeometry(m: Mesh): Geometry | null {
  if (!m.indices.length) return null;
  const pos = frame.enuArrayToWorld(m.positions);
  return new Geometry({
    attributes: { position: new GeometryAttribute({ componentDatatype: ComponentDatatype.DOUBLE, componentsPerAttribute: 3, values: pos }) } as never,
    indices: new Uint32Array(m.indices),
    primitiveType: PrimitiveType.TRIANGLES,
    boundingSphere: BoundingSphere.fromVertices(Array.from(pos)),
  });
}

function prim(instances: GeometryInstance[]): Primitive {
  return viewer.scene.primitives.add(
    new Primitive({ geometryInstances: instances, appearance: new PerInstanceColorAppearance({ flat: true, translucent: false }), asynchronous: false }),
  );
}

function inst(id: unknown, m: Mesh, css: string): GeometryInstance | null {
  const g = toGeometry(m);
  return g && new GeometryInstance({ id, geometry: g, attributes: { color: ColorGeometryInstanceAttribute.fromColor(Color.fromCssColorString(css)) } });
}

// ------------------------------------------------------------------ state

let scene: Scene | null = null;
let heights: HeightIndex | null = null;
let lanesById = new Map<string, Lane>();
let tracks: Track[] = [];
let events: TwinEvent[] = [];
let evIdx: EventIndex = new Map();
let t = 0, tMin = 0, tMax = 0, playing = false;
let mode: "replay" | "live" = "replay";
let liveVehicles: VehicleState[] = [];
let ws: WebSocket | null = null;
let selectedLane: Lane | null = null;
const pendingOverrides: LaneOverride[] = [];
const pendingZones: Zone[] = [];
let drawing: [number, number][] | null = null;

const z = (x: number, y: number) => (heights ? heights.heightAt(x, y) : 0);
const laneColor = (l: Lane) => CATEGORY_COLOR[laneCategory(l)] ?? CATEGORY_COLOR.driving;

// ------------------------------------------------------------------ roads

function buildRoads(sc: Scene) {
  roadPrims.forEach((p) => viewer.scene.primitives.remove(p));
  roadPrims = [];
  heights = new HeightIndex(sc.lanes);
  lanesById = new Map(sc.lanes.map((l) => [l.id, l]));
  const laneInst: GeometryInstance[] = [];
  const marks = { white: emptyMesh(), yellow: emptyMesh(), curb: emptyMesh(), grass: emptyMesh() };
  let median = emptyMesh(), piers = emptyMesh(), cross = emptyMesh();
  for (const l of sc.lanes) {
    const m = buildLaneMeshes(l);
    const i = inst(l.id, m.surface, laneColor(l));
    if (i) laneInst.push(i);
    for (const k of Object.keys(marks) as (keyof typeof marks)[]) marks[k] = mergeInto(marks[k], m.marks[k]);
    median = mergeInto(median, m.median);
    piers = mergeInto(piers, m.piers);
  }
  for (const zn of crosswalkZones(sc.zones)) {
    const [cx, cy] = zn.polygon[0];
    // CARLA lists some crosswalks away from every exported road (Town03: several outside the lane
    // extent); drawn alone they float in empty space
    if (!heights.levels(cx, cy, 15).length) continue;
    cross = mergeInto(cross, polygonFan(zn.polygon, z(cx, cy), 0.07)); // polygonFan converts CARLA -> ENU itself
  }
  lanePrim = prim(laneInst);
  roadPrims.push(lanePrim);
  const extra = [
    inst("marks-white", marks.white, "#f1f3f5"), inst("marks-yellow", marks.yellow, "#fcc419"), inst("curb", marks.curb, "#adb5bd"),
    inst("grass", marks.grass, "#69db7c"), inst("median", median, "#5c940d"), inst("piers", piers, "#868e96"), inst("crosswalks", cross, "#f8f9fa"),
  ].filter((x): x is GeometryInstance => !!x);
  if (extra.length) roadPrims.push(prim(extra));
  // frame the town
  const xs = sc.lanes.flatMap((l) => l.centreline.map((p) => p[0]));
  const ys = sc.lanes.flatMap((l) => l.centreline.map((p) => p[1]));
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const r = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) / 2;
  viewer.camera.flyToBoundingSphere(new BoundingSphere(frame.carla(cx, cy, 0), r), { duration: 0 });
  applyLaneColors();
}

function applyLaneColors() {
  if (!lanePrim || !scene) return;
  // per-instance attributes exist only after the primitive's first update (first rendered frame);
  // calling earlier throws "must call update before calling getGeometryInstanceAttributes"
  try {
    lanePrim.getGeometryInstanceAttributes(scene.lanes[0]?.id);
  } catch {
    const remove = viewer.scene.postRender.addEventListener(() => {
      remove();
      applyLaneColors();
    });
    return;
  }
  const heat = ($("heat") as HTMLInputElement).checked;
  const counts = new Map<string, number>();
  if (heat) for (const e of events) if (e.lane_id) counts.set(e.lane_id, (counts.get(e.lane_id) ?? 0) + 1);
  const max = Math.max(1, ...counts.values());
  for (const l of scene.lanes) {
    const a = lanePrim.getGeometryInstanceAttributes(l.id);
    if (!a) continue;
    let c = Color.fromCssColorString(laneColor(l));
    if (heat) {
      const n = counts.get(l.id) ?? 0;
      c = n ? Color.lerp(Color.fromCssColorString("#ffd43b"), Color.fromCssColorString("#c92a2a"), n / max, new Color()) : Color.fromCssColorString("#5c5f66");
    }
    if (selectedLane?.id === l.id) c = Color.fromCssColorString("#15aabf");
    a.color = ColorGeometryInstanceAttribute.toValue(c);
  }
}

// ------------------------------------------------------------------ vehicles, trails, pins

let lastDrawn = 0;
function drawVehicles(states: VehicleState[]) {
  lastDrawn = states.length;
  points.removeAll();
  labels.removeAll();
  trails.removeAll();
  for (const v of states) {
    const pos = frame.carla(v.x, v.y, z(v.x, v.y) + 1.2);
    points.add({ position: pos, pixelSize: 10, disableDepthTestDistance: Number.POSITIVE_INFINITY, color: STATE_COLOR[v.state ?? "ok"], outlineColor: Color.BLACK, outlineWidth: 1, id: { kind: "vehicle", v } });
    if (v.speed_kmh != null)
      labels.add({ position: pos, text: `${Math.round(v.speed_kmh)}`, font: "11px sans-serif", disableDepthTestDistance: Number.POSITIVE_INFINITY, pixelOffset: new Cartesian2(8, -8),
        style: LabelStyle.FILL_AND_OUTLINE, outlineWidth: 2, outlineColor: Color.BLACK, fillColor: Color.WHITE });
    if (mode === "replay") {
      const tr = tracks.find((k) => k.id === String(v.track_id));
      if (!tr) continue;
      const ps: Cartesian3[] = [];
      for (let s = t - 3; s <= t; s += 0.25) {
        const p = sampleTrack(tr, s);
        if (p) ps.push(frame.carla(p.x, p.y, z(p.x, p.y) + 1.0));
      }
      if (ps.length > 1) trails.add({ positions: ps, width: 2, material: Material.fromType("Color", { color: STATE_COLOR[v.state ?? "ok"].withAlpha(0.6) }) });
    }
  }
}

function drawPins(upTo: number | null) {
  pins.removeAll();
  for (const e of events) {
    if (upTo != null && eventTime(e) > upTo) continue;
    pins.add({ position: frame.carla(e.x, e.y, z(e.x, e.y) + 4), pixelSize: 14, disableDepthTestDistance: Number.POSITIVE_INFINITY, color: Color.fromCssColorString(TYPE_COLOR[e.type] ?? "#fa5252"),
      outlineColor: Color.WHITE, outlineWidth: 2, id: { kind: "event", e } });
  }
}

function render() {
  if (mode === "replay") {
    drawVehicles(statesAt(tracks, t, evIdx));
    drawPins(t);
    $("clock").textContent = `t = ${t.toFixed(1)} s (${(t - tMin).toFixed(0)} / ${(tMax - tMin).toFixed(0)} s) · ${tracks.length} tracks, ${lastDrawn} cars shown, ${events.length} events`;
    ($("time") as HTMLInputElement).value = String(t);
  } else {
    drawVehicles(liveVehicles);
    drawPins(null);
  }
}

let last = 0;
function tick(now: number) {
  if (playing && mode === "replay") {
    t = Math.min(tMax, t + ((now - last) / 1000) * Number(($("speed") as HTMLSelectElement).value));
    if (t >= tMax) setPlaying(false);
    render();
  }
  last = now;
  requestAnimationFrame(tick);
}

function setPlaying(p: boolean) {
  playing = p;
  $("play").textContent = p ? "⏸" : "▶";
}

// ------------------------------------------------------------------ data loading

async function loadTown(town: string) {
  scene = await api.scene(town);
  buildRoads(scene);
}

async function loadSession(id: string) {
  const s = await api.session(id);
  const town = s.town ?? s.scene ?? null;
  if (town && town !== scene?.scene) {
    ($("town") as HTMLSelectElement).value = town;
    await loadTown(town);
  }
  events = await api.events(id);
  evIdx = indexEventsByTrack(events);
  tracks = parseTrajectories(await api.trajectories(id));
  [tMin, tMax] = timeRange(tracks);
  const el = $("time") as HTMLInputElement;
  el.min = String(tMin);
  el.max = String(tMax);
  const want = Number(qs.get("t"));
  t = Number.isFinite(want) && want >= tMin && want <= tMax ? want : tMin;
  applyLaneColors();
  if (mode === "live") connectLive(id);
  render();
}

function connectLive(id: string | null) {
  ws?.close();
  ws = null;
  liveVehicles = [];
  if (mode !== "live") return;
  $("liveStatus").textContent = "connecting…";
  ws = new WebSocket(api.liveUrl(id ?? undefined));
  ws.onopen = () => ($("liveStatus").textContent = "live");
  ws.onclose = () => ($("liveStatus").textContent = "closed");
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data as string);
    if (msg.type === "vehicles") liveVehicles = msg.items;
    else if (msg.type === "event") events.push(msg.event);
    render();
  };
}

// ------------------------------------------------------------------ selection and planner edits

function kv(o: Record<string, unknown>): string {
  return `<table class="kv">${Object.entries(o)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `<tr><td>${k}</td><td>${Array.isArray(v) ? v.join(", ") : String(v)}</td></tr>`)
    .join("")}</table>`;
}

function select(picked: unknown) {
  selectedLane = null;
  const p = picked as { id?: unknown } | undefined;
  const id = p?.id as { kind?: string; v?: VehicleState; e?: TwinEvent } | string | undefined;
  const box = $("selected");
  if (typeof id === "string" && lanesById.has(id)) {
    const l = lanesById.get(id)!;
    selectedLane = l;
    const { centreline: _c, z: _z, next: _n, ...rest } = l;
    box.innerHTML = kv(rest as Record<string, unknown>);
    ($("limit") as HTMLInputElement).value = l.speed_limit_kmh != null ? String(l.speed_limit_kmh) : "";
    ($("restricted") as HTMLSelectElement).value = l.restricted ?? "";
  } else if (id && typeof id === "object" && id.kind === "vehicle" && id.v) {
    box.innerHTML = kv({ track: id.v.track_id, state: id.v.state, speed_kmh: id.v.speed_kmh?.toFixed(1), heading: id.v.heading_deg?.toFixed(0) });
  } else if (id && typeof id === "object" && id.kind === "event" && id.e) {
    const e = id.e;
    box.innerHTML = kv({ type: e.type, condition: e.condition, tracks: e.track_ids, time_s: eventTime(e).toFixed(1), lane: e.lane_id, status: e.status, value: JSON.stringify(e.value ?? {}) });
  } else {
    box.textContent = "Click a lane, vehicle or pin.";
  }
  applyLaneColors();
}

function renderPending() {
  $("pending").innerHTML =
    pendingOverrides.map((o) => `<li>lane ${o.lane_id}: ${o.speed_limit_kmh ? `${o.speed_limit_kmh} km/h ` : ""}${o.restricted ? o.restricted : ""}</li>`).join("") +
    pendingZones.map((zn) => `<li>zone ${zn.type} (${zn.polygon.length} pts)</li>`).join("") || "<li class='muted'>none</li>";
}

function addOverride() {
  if (!selectedLane || !scene) return;
  const lim = ($("limit") as HTMLInputElement).value;
  const res = ($("restricted") as HTMLSelectElement).value;
  const o: LaneOverride = { lane_id: overrideTarget(selectedLane.id, ($("roadScope") as HTMLInputElement).checked ? "road_dir" : "lane") };
  if (lim && Number(lim) !== selectedLane.speed_limit_kmh) o.speed_limit_kmh = Number(lim);
  if ((res || null) !== (selectedLane.restricted ?? null)) o.restricted = (res || null) as LaneOverride["restricted"];
  const errs = validateOverride(o, scene.lanes);
  if (o.speed_limit_kmh === undefined && o.restricted === undefined) errs.push("nothing changed");
  $("saveErr").textContent = errs.join("\n");
  if (!errs.length) {
    pendingOverrides.push(o);
    renderPending();
  }
}

function drawPreview() {
  drawn.removeAll();
  const poly = drawing ?? [];
  if (poly.length > 1)
    drawn.add({ positions: [...poly, poly[0]].map(([x, y]) => frame.carla(x, y, z(x, y) + 0.5)), width: 3,
      material: Material.fromType("Color", { color: Color.ORANGE }) });
}

async function save() {
  const name = ($("profileName") as HTMLInputElement).value.trim();
  const note = ($("note") as HTMLTextAreaElement).value.trim();
  const errs = [validateProfileName(name), validateNote(note)].filter(Boolean) as string[];
  if (!pendingOverrides.length && !pendingZones.length) errs.push("no pending edits");
  $("saveErr").textContent = errs.join("\n");
  if (errs.length) return;
  let base: Record<string, any>;
  try {
    base = unwrapProfile(await api.profile(name));
  } catch {
    base = { ...unwrapProfile(await api.profile("default")), description: `Planner edits on ${scene?.scene}`, applies_to: { map: scene?.scene ?? null } };
  }
  const profile = buildProfile(base, name, pendingOverrides);
  if (pendingZones.length) profile.road = { ...profile.road, extra_zones: [...(profile.road?.extra_zones ?? []), ...pendingZones] };
  try {
    await api.putProfile(name, { profile, note });
    pendingOverrides.length = 0;
    pendingZones.length = 0;
    renderPending();
    $("saveErr").textContent = "";
    $("saveErr").insertAdjacentHTML("beforeend", `<span style="color:#40c057">Saved as a new version of ${name}. The engine uses it with --profile ${name}.</span>`);
    loadHistory(name);
  } catch (e) {
    $("saveErr").textContent = (e as Error).message;
  }
}

async function loadHistory(name: string) {
  try {
    const h = await api.profileHistory(name);
    $("history").innerHTML = h.map((r: any) => `<li>v${r.version ?? "?"} · ${r.by ?? "?"} · ${(r.at ?? "").slice(0, 16)}<br>${r.note ?? ""}</li>`).join("") || "<li>none</li>";
  } catch {
    $("history").innerHTML = "<li>none yet</li>";
  }
}

// ------------------------------------------------------------------ wiring

const handler = new ScreenSpaceEventHandler(viewer.scene.canvas);
handler.setInputAction((ev: { position: Cartesian2 }) => {
  if (drawing) {
    const p = viewer.camera.pickEllipsoid(ev.position);
    if (p) {
      const [x, y] = frame.toCarla(p);
      drawing.push([Math.round(x * 100) / 100, Math.round(y * 100) / 100]);
      drawPreview();
    }
    return;
  }
  select(viewer.scene.pick(ev.position));
}, ScreenSpaceEventType.LEFT_CLICK);
handler.setInputAction(() => {
  if (!drawing) return;
  const zn: Zone = { id: `twin_${Date.now()}`, type: ($("zoneType") as HTMLSelectElement).value, polygon: drawing };
  const errs = validateZone(zn);
  $("saveErr").textContent = errs.join("\n");
  if (!errs.length) pendingZones.push(zn);
  drawing = null;
  $("drawHint").textContent = "";
  drawPreview();
  renderPending();
}, ScreenSpaceEventType.RIGHT_CLICK);

$("drawZone").onclick = () => {
  drawing = [];
  $("drawHint").textContent = "left-click corners, right-click to finish";
};
$("addOverride").onclick = addOverride;
$("save").onclick = save;
$("play").onclick = () => setPlaying(!playing);
$("time").oninput = (e) => {
  t = Number((e.target as HTMLInputElement).value);
  render();
};
$("heat").onchange = applyLaneColors;
$("session").onchange = (e) => loadSession((e.target as HTMLSelectElement).value);
$("town").onchange = (e) => loadTown((e.target as HTMLSelectElement).value);
document.querySelectorAll<HTMLInputElement>("input[name=mode]").forEach((r) =>
  r.addEventListener("change", () => {
    mode = r.value as typeof mode;
    setPlaying(false);
    connectLive(($("session") as HTMLSelectElement).value || null);
    render();
  }),
);
window.addEventListener("message", (m) => {
  const d = m.data as { type?: string; t?: number; id?: string };
  if (d?.type === "seek" && typeof d.t === "number") {
    t = Math.max(tMin, Math.min(tMax, d.t));
    render();
  } else if (d?.type === "session" && d.id) loadSession(d.id);
});

async function start() {
  $("login").hidden = true;
  $("main").hidden = false;
  $("user").textContent = `${api.username} (${api.role})`;
  $("editor").hidden = !api.canEdit();
  renderPending();
  const scenes = (await api.scenes()) as unknown[];
  const towns = scenes.map((s) => (typeof s === "string" ? s : String((s as any).town ?? (s as any).scene ?? (s as any).name))).map((s) => s.replace(/\.json$/, ""));
  $("town").innerHTML = towns.map((s) => `<option>${s}</option>`).join("");
  const sessions = await api.sessions();
  $("session").innerHTML = sessions.map((s) => `<option value="${s.session_id}">${s.name ?? s.session_id}</option>`).join("");
  const want = qs.get("session") ?? sessions[0]?.session_id;
  const town = qs.get("town") ?? towns[0];
  if (want) {
    ($("session") as HTMLSelectElement).value = want;
    await loadSession(want);
  } else if (town) await loadTown(town);
  const profiles = profileSummaries(await api.profiles());
  ($("profileName") as HTMLInputElement).placeholder = profiles.find((p) => p.name.includes("planner"))?.name ?? `${(scene?.scene ?? "town").toLowerCase()}_planner`;
  requestAnimationFrame(tick);
}

$("loginBtn").onclick = async () => {
  try {
    await api.login(($("username") as HTMLInputElement).value, ($("password") as HTMLInputElement).value);
    await start();
  } catch (e) {
    $("loginErr").textContent = (e as Error).message;
  }
};
