// 3D digital twin (Build Plan M7) inside the dashboard: the lane map in 3D (heights, bridges, markings,
// crosswalks), town objects, vehicles live (WebSocket) or replayed from a session's trajectories,
// violation pins, hotspots, problem sections, zones (GET/POST/PUT/DELETE /api/zones), lane edits saved
// as a new profile version, and what-if scenarios. Ported from the standalone twin/ app (port 5174):
// the dashboard's login, tokens and api client are used, and the page (pages/Twin.tsx) mounts one
// TwinView in a div and disposes it on unmount.

import "./cesiumBase"; // first: sets CESIUM_BASE_URL before Cesium runs
import "cesium/Build/Cesium/Widgets/widgets.css";
import "./twin.css";
import {
  BoundingSphere,
  BoxGeometry,
  Cartesian2,
  Cartesian3,
  Color,
  ColorGeometryInstanceAttribute,
  ComponentDatatype,
  CylinderGeometry,
  EllipsoidGeometry,
  Geometry,
  GeometryAttribute,
  GeometryInstance,
  LabelCollection,
  LabelStyle,
  Material,
  Matrix3,
  Matrix4,
  PerInstanceColorAppearance,
  PointPrimitiveCollection,
  PolylineCollection,
  Primitive,
  PrimitiveType,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  Transforms,
  Viewer,
} from "cesium";
import type { Role, ZoneFeature, ZoneType } from "../api/types";
import { can } from "../lib/permissions";
import { openRing, polygonProblem, zoneColor, ZONE_TYPES } from "../lib/zones";
import { profileSummaries, twinApi as api, unwrapProfile } from "./api";
import { DEFAULT_ANCHOR, Frame, parseAnchor } from "./coords";
import { buildLaneMeshes, crosswalkZones, emptyMesh, HeightIndex, laneCategory, mergeInto, polygonFan, type Mesh } from "./laneGeometry";
import { gridHotspots, isAnomaly, LiveTrails, pinStyle, problemSections } from "./layers";
import { describeItem, FALLBACK_ATTRIBUTES, laneValue, OVERRIDE_KEYS, type AttrValue, type OverrideKey } from "../lib/roadEdits";
import type { RoadAttribute } from "../api/types";
import { buildProfile, overrideTarget, validateNote, validateOverride, validateProfileName } from "./planEdits";
import { eventTime, indexEventsByTrack, parseTrajectories, sampleTrack, statesAt, timeRange, type EventIndex, type Track } from "./replay";
import type { Lane, LaneOverride, Scene, TwinEvent, VehicleState, Zone } from "./types";
import { localTime } from "./whatif";
import { PlanBuilder, type BuilderHost, type DrawnItem } from "./builder/PlanBuilder";

export interface TwinOptions {
  username: string;
  role: Role;
  session?: string | null;
  t?: number | null;
  town?: string | null;
  anchor?: string | null;
  mode?: "replay" | "live";
  dark?: boolean;
  /** the session picked in the panel (the page mirrors it into ?session=) */
  onSession?: (id: string) => void;
}

/** A zone drawn in the twin, not yet sent to /api/zones. */
interface PendingZone extends Zone {
  name: string;
  grace_s?: number;
  limit_kmh?: number;
}

const STATE_COLOR: Record<string, Color> = {
  ok: Color.fromCssColorString("#40c057"),
  checking: Color.fromCssColorString("#fab005"),
  flagged: Color.fromCssColorString("#fa5252"),
};
const CATEGORY_COLOR: Record<string, string> = {
  bridge: "#7048e8", highway: "#4c6ef5", ramp: "#e64980", junction: "#868e96", shoulder: "#adb5bd",
  parking: "#ced4da", restricted: "#fd7e14", driving: "#495057",
};
const OBJECT_COLOR: Record<string, string> = {
  Buildings: "#c9ccd1", Vegetation: "#2f9e44", Poles: "#868e96", Walls: "#b8bcc2", Fences: "#9aa0a6",
  GuardRail: "#dee2e6", Bridge: "#9775fa", RailTrack: "#845ef7", TrafficLight: "#fab005", TrafficSigns: "#fa5252",
  Static: "#8d8f94", Dynamic: "#e8590c",
};
const HOT_R = 12.5; // hotspot column radius = half the 25 m grid cell
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

const zoneTypeOptions = (Object.keys(ZONE_TYPES) as ZoneType[])
  .map((t) => `<option value="${t}">${esc(ZONE_TYPES[t].label)}</option>`).join("");

/** One row per road attribute in the planner panel (`attr_<key>`), same attributes as Configuration > Road configuration. */
function attrRowsHtml(): string {
  return FALLBACK_ATTRIBUTES.map((a) => {
    const title = esc(`drives ${a.drives.map((d) => d.condition).join(", ")}`);
    const control = a.type === "number"
      ? `<input id="attr_${a.key}" type="number" min="5" max="130" />`
      : `<select id="attr_${a.key}">${a.type === "bool"
        ? `<option value="true">yes</option><option value="false">no</option>`
        : (a.values ?? []).map((v) => `<option value="${v ?? ""}">${v ?? "none"}</option>`).join("")}</select>`;
    return `<label title="${title}">${esc(a.label)} ${control}</label>`;
  }).join("");
}

function setAttrControl(el: HTMLElement, _a: RoadAttribute, v: AttrValue) {
  (el as HTMLInputElement | HTMLSelectElement).value = v === null || v === undefined ? "" : String(v);
}

/** The control's value as an override value; undefined = empty number box. */
function readAttrControl(el: HTMLElement, a: RoadAttribute): AttrValue | undefined {
  const raw = (el as HTMLInputElement | HTMLSelectElement).value;
  if (a.type === "number") return raw === "" ? undefined : Number(raw);
  if (a.type === "bool") return raw === "true";
  return raw === "" ? null : raw;
}

const PANEL_HTML = `
<div class="tw-viewer"></div>
<button id="panelToggle" class="tw-toggle" title="Show / hide the panel">☰</button>
<aside class="tw-panel">
  <header><strong>3D Digital Twin</strong><span id="user" class="muted"></span></header>
  <label class="tw-switch" title="Show or hide the vehicles (with their trails and speeds) coming from the video / live feed"><input type="checkbox" id="traffic" checked /><span class="tw-track"></span><span class="tw-switch-text">Traffic from video</span></label>
  <label class="tw-switch" id="customSw" title="Virtual traffic on the map (blue): plain traffic on its own, and it follows your scenario plan as soon as the plan has items. Simplified model, not CARLA."><input type="checkbox" id="custom" /><span class="tw-track"></span><span class="tw-switch-text">Custom traffic <span class="muted" id="customNote">(off)</span></span><button type="button" class="gear" id="customCfg" title="Configure the custom traffic: density, vehicle mix, drivers, demand over time, sources, groups, zones">⚙</button></label>
  <label>Session <select id="session"></select></label>
  <label>Town <select id="town"></select></label>
  <div class="row tw-traffic">
    <label><input type="radio" name="tw-mode" value="replay" checked /> Replay</label>
    <label><input type="radio" name="tw-mode" value="live" /> Live</label>
    <span id="liveStatus" class="muted"></span>
  </div>
  <div class="row tw-traffic">
    <button id="play">▶</button>
    <input id="time" type="range" min="0" max="1" step="0.1" value="0" />
    <select id="speed"><option>0.5</option><option>1</option><option selected>2</option><option>4</option><option>8</option></select>
  </div>
  <div class="muted tw-traffic" id="clock"></div>
  <div class="row"><label><input type="checkbox" id="heat" /> Problem sections (events per lane)</label></div>
  <div class="row">
    <label><input type="checkbox" id="hotspots" checked /> Hotspots</label>
    <label><input type="checkbox" id="objects" checked /> Buildings &amp; objects</label>
  </div>
  <div class="row"><label><input type="checkbox" id="zonesOn" checked /> Zones</label><span id="zoneCount" class="muted"></span></div>
  <label>Pins <select id="pinKind"><option value="violation" selected>violations</option><option value="all">violations &amp; road surface</option><option value="anomaly">road surface</option><option value="none">none</option></select></label>
  <div class="legend">
    <span class="tw-traffic" style="--c:#40c057">ok</span><span class="tw-traffic" style="--c:#fab005">checking</span><span class="tw-traffic" style="--c:#fa5252">flagged</span>
    <span style="--c:#7048e8">bridge</span><span style="--c:#4c6ef5">highway</span>
  </div>
  <details open><summary><strong>Hotspots</strong> <span class="muted">(25 m cells, click to fly)</span></summary><ol id="hotList" class="clicky"></ol></details>
  <details><summary><strong>Problem road sections</strong> <span class="muted">(lanes by events)</span></summary><ol id="sections" class="clicky"></ol></details>
  <h4>Selected</h4>
  <div id="selected" class="muted">Click a lane, vehicle, pin or zone.</div>
</aside>
<button id="plannerToggle" class="tw-toggle tw-toggle-left" title="Show / hide the planner panel" hidden>&#9998;</button>
<aside class="tw-panel tw-planner" id="editor" hidden>
  <div>
    <header><strong>Planner</strong><span class="muted">select, edit, validate &amp; save</span></header>
    <div id="plannerHint" class="hint"><strong>Nothing selected</strong><span id="plannerHintText">Click a lane or a zone on the map and its editor appears here.</span></div>
    <div id="zoneActions" hidden>
      <h4>Selected zone</h4>
      <div class="row"><button id="zoneRedraw">Redraw outline</button><button id="zoneDeactivate">Deactivate</button></div>
      <ul id="zoneHistory" class="muted"></ul>
    </div>
    <div id="laneEdit" hidden>
      <h4>Selected lane</h4>
      ${attrRowsHtml()}
      <label><input type="checkbox" id="roadScope" /> whole road direction</label>
      <button id="addOverride">Add lane change</button>
      <hr />
    </div>
    <details id="zoneEditBox"><summary><strong>New zone</strong></summary>
    <div id="zoneEdit">
      <label>Zone type <select id="zoneType">${zoneTypeOptions}</select></label>
      <label>Zone name <input id="zoneName" placeholder="e.g. school gate no stopping" /></label>
      <label id="zoneParamRow"><span id="zoneParamLabel">Grace (s)</span> <input id="zoneParam" type="number" min="0" /></label>
      <button id="drawZone">Draw zone</button> <span id="drawHint" class="muted"></span>
    </div>
    </details>
    <div id="saveBox" hidden>
    <h4>Pending edits</h4>
    <ul id="pending"></ul>
    <label>Profile (lane changes) <input id="profileName" placeholder="town03_planner" /></label>
    <label>Why (logged) <textarea id="note" rows="2"></textarea></label>
    <button id="save">Validate &amp; save</button>
    </div>
    <div class="err" id="saveErr"></div>
    <h4>Edit history</h4>
    <ul id="history" class="muted"></ul>
  </div>
</aside>`;

export class TwinView {
  private readonly root: HTMLElement;
  private readonly opts: TwinOptions;
  private readonly viewer: Viewer;
  private readonly frame: Frame;
  private readonly handler: ScreenSpaceEventHandler;
  private readonly points: PointPrimitiveCollection;
  private readonly labels: LabelCollection;
  private readonly trails: PolylineCollection;
  private readonly pins: PointPrimitiveCollection;
  private readonly pinLabels: LabelCollection;
  private readonly hotLabels: LabelCollection;
  private readonly drawn: PolylineCollection;
  private readonly zoneLines: PolylineCollection;
  private readonly zoneLabels: LabelCollection;
  private hotPrim: Primitive | null = null;
  private zoneFill: Primitive | null = null;
  private roadPrims: Primitive[] = [];
  private lanePrim: Primitive | null = null;
  private objectPrim: Primitive | null = null;

  private disposed = false;
  private raf = 0;
  private scene: Scene | null = null;
  private heights: HeightIndex | null = null;
  private lanesById = new Map<string, Lane>();
  private tracks: Track[] = [];
  private events: TwinEvent[] = [];
  private evIdx: EventIndex = new Map();
  private t = 0;
  private tMin = 0;
  private tMax = 0;
  private playing = false;
  private mode: "replay" | "live" = "replay";
  private liveVehicles: VehicleState[] = [];
  private ws: WebSocket | null = null;
  private readonly liveTrails = new LiveTrails();
  private pendingSeek: number | null = null;
  private selectedLane: Lane | null = null;
  private selectedZone: ZoneFeature | null = null;
  private apiZones: ZoneFeature[] = [];
  private readonly pendingOverrides: LaneOverride[] = [];
  private readonly pendingZones: PendingZone[] = [];
  private drawing: [number, number][] | null = null;
  private redrawZone: ZoneFeature | null = null;
  private builder!: PlanBuilder;
  /** custom (virtual) traffic of the scenario builder: its own layer, looped on the replay clock */
  private custom: { tracks: Track[]; t0: number; dur: number; ownClock: boolean } | null = null;
  private readonly customPoints: PointPrimitiveCollection;
  private readonly customLabels: LabelCollection;
  private readonly planPoints: PointPrimitiveCollection;
  private readonly planLabels: LabelCollection;
  private readonly planLines: PolylineCollection;
  private ctxMenu: HTMLElement | null = null;
  private lastDrawn = 0;
  private last = 0;
  private sessionToken = 0; // bumps on every loadSession: a slower older load must not win

  constructor(root: HTMLElement, opts: TwinOptions) {
    this.root = root;
    this.opts = opts;
    root.classList.add("twin-root");
    root.innerHTML = PANEL_HTML;
    this.frame = new Frame(parseAnchor(opts.anchor) ?? DEFAULT_ANCHOR);
    this.viewer = new Viewer(root.querySelector<HTMLElement>(".tw-viewer")!, {
      baseLayer: false, baseLayerPicker: false, geocoder: false, timeline: false, animation: false,
      homeButton: false, sceneModePicker: false, navigationHelpButton: false, fullscreenButton: false,
      infoBox: false, selectionIndicator: false,
    });
    // CARLA towns have no real terrain or imagery: hide the globe (no z-fighting with roads at z = 0)
    const sc = this.viewer.scene;
    sc.globe.show = false;
    if (sc.skyAtmosphere) sc.skyAtmosphere.show = false;
    if (sc.skyBox) sc.skyBox.show = false;
    this.setDark(!!opts.dark);
    this.points = sc.primitives.add(new PointPrimitiveCollection());
    this.labels = sc.primitives.add(new LabelCollection());
    this.trails = sc.primitives.add(new PolylineCollection());
    this.zoneLines = sc.primitives.add(new PolylineCollection());
    this.zoneLabels = sc.primitives.add(new LabelCollection());
    this.pins = sc.primitives.add(new PointPrimitiveCollection());
    this.pinLabels = sc.primitives.add(new LabelCollection());
    this.hotLabels = sc.primitives.add(new LabelCollection());
    this.drawn = sc.primitives.add(new PolylineCollection());
    this.customPoints = sc.primitives.add(new PointPrimitiveCollection());
    this.customLabels = sc.primitives.add(new LabelCollection());
    this.planLines = sc.primitives.add(new PolylineCollection());
    this.planPoints = sc.primitives.add(new PointPrimitiveCollection());
    this.planLabels = sc.primitives.add(new LabelCollection());
    this.handler = new ScreenSpaceEventHandler(sc.canvas);
    const host: BuilderHost = {
      root, role: opts.role, town: () => this.scene?.scene ?? "", sessionId: () => this.val("session"),
      flyTo: (x, y) => this.flyTo(x, y), draw: (items, anchor) => this.drawPlan(items, anchor),
      player: {
        toggle: () => this.setPlaying(!this.playing),
        reset: () => { this.t = this.tMin; this.render(); },
        stop: () => { this.setPlaying(false); this.t = this.tMin; this.render(); },
        seek: (f) => { this.t = this.tMin + (this.tMax - this.tMin) * f; this.render(); },
        setSpeed: (v) => { (this.$("speed") as HTMLSelectElement).value = String(v); },
        read: () => ({ t: this.t, tMin: this.tMin, tMax: this.tMax, playing: this.playing }),
      },
      showVirtual: (traj) => this.showVirtual(traj),
      customTraffic: { isOn: () => this.checked("custom"), set: (on) => this.setCustomTraffic(on) },
      trafficInfo: () => this.lastTraffic,
      planChanged: () => this.onPlanChanged(),
      screenshot: () => {
        try {
          this.viewer.render();
          const c = this.viewer.canvas as HTMLCanvasElement;
          const w = Math.min(c.width, 1100), r = w / c.width;
          const o = document.createElement("canvas");
          o.width = w;
          o.height = Math.max(1, Math.round(c.height * r));
          o.getContext("2d")!.drawImage(c, 0, 0, o.width, o.height);
          return o.toDataURL("image/png");
        } catch {
          return null;
        }
      },
    };
    this.builder = new PlanBuilder(host);
    this.builder.mount();
    this.wire();
    void this.guard(this.start());
  }

  // ------------------------------------------------------------------ helpers

  private $<T extends HTMLElement = HTMLElement>(id: string): T {
    return this.root.querySelector(`#${id}`) as T;
  }
  private val(id: string): string {
    return (this.$(id) as HTMLInputElement).value;
  }
  private checked(id: string): boolean {
    return (this.$(id) as HTMLInputElement).checked;
  }
  /** Runtime errors go to the panel (the people using the twin have no console). */
  private async guard(p: Promise<unknown>): Promise<void> {
    try {
      await p;
    } catch (e) {
      if (this.disposed) return;
      const el = this.$("saveErr") ?? this.$("clock");
      if (el) el.textContent = `Error: ${(e as Error)?.message ?? String(e)}`;
    }
  }
  private z = (x: number, y: number) => (this.heights ? this.heights.heightAt(x, y) : 0);
  private laneColor = (l: Lane) => CATEGORY_COLOR[laneCategory(l)] ?? CATEGORY_COLOR.driving;
  private canEditProfile = () => can(this.opts.role, "edit_profile");
  private canEditZone = () => can(this.opts.role, "edit_zone");

  setDark(dark: boolean) {
    if (this.disposed) return;
    this.viewer.scene.backgroundColor = Color.fromCssColorString(dark ? "#141517" : "#dfe5df");
  }

  private toGeometry(m: Mesh): Geometry | null {
    if (!m.indices.length) return null;
    const pos = this.frame.enuArrayToWorld(m.positions);
    return new Geometry({
      attributes: { position: new GeometryAttribute({ componentDatatype: ComponentDatatype.DOUBLE, componentsPerAttribute: 3, values: pos }) } as never,
      indices: new Uint32Array(m.indices),
      primitiveType: PrimitiveType.TRIANGLES,
      boundingSphere: BoundingSphere.fromVertices(Array.from(pos)),
    });
  }

  private prim(instances: GeometryInstance[], translucent = false): Primitive {
    return this.viewer.scene.primitives.add(
      new Primitive({ geometryInstances: instances, appearance: new PerInstanceColorAppearance({ flat: true, translucent }), asynchronous: false }),
    );
  }

  private inst(id: unknown, m: Mesh, css: string, alpha = 1): GeometryInstance | null {
    const g = this.toGeometry(m);
    return g && new GeometryInstance({ id, geometry: g, attributes: { color: ColorGeometryInstanceAttribute.fromColor(Color.fromCssColorString(css).withAlpha(alpha)) } });
  }

  // ------------------------------------------------------------------ roads

  private buildRoads(sc: Scene) {
    const prims = this.viewer.scene.primitives;
    this.roadPrims.forEach((p) => prims.remove(p));
    this.roadPrims = [];
    this.heights = new HeightIndex(sc.lanes);
    this.lanesById = new Map(sc.lanes.map((l) => [l.id, l]));
    const laneInst: GeometryInstance[] = [];
    const marks = { white: emptyMesh(), yellow: emptyMesh(), curb: emptyMesh(), grass: emptyMesh() };
    let median = emptyMesh(), piers = emptyMesh(), cross = emptyMesh();
    for (const l of sc.lanes) {
      const m = buildLaneMeshes(l);
      const i = this.inst(l.id, m.surface, this.laneColor(l));
      if (i) laneInst.push(i);
      for (const k of Object.keys(marks) as (keyof typeof marks)[]) marks[k] = mergeInto(marks[k], m.marks[k]);
      median = mergeInto(median, m.median);
      piers = mergeInto(piers, m.piers);
    }
    for (const zn of crosswalkZones(sc.zones)) {
      const [cx, cy] = zn.polygon[0];
      // CARLA lists some crosswalks away from every exported road (Town03: several outside the lane
      // extent); drawn alone they float in empty space
      if (!this.heights.levels(cx, cy, 15).length) continue;
      cross = mergeInto(cross, polygonFan(zn.polygon, this.z(cx, cy), 0.07)); // polygonFan converts CARLA -> ENU itself
    }
    this.lanePrim = laneInst.length ? this.prim(laneInst) : null;
    if (this.lanePrim) this.roadPrims.push(this.lanePrim);
    const extra = [
      this.inst("marks-white", marks.white, "#f1f3f5"), this.inst("marks-yellow", marks.yellow, "#fcc419"), this.inst("curb", marks.curb, "#adb5bd"),
      this.inst("grass", marks.grass, "#69db7c"), this.inst("median", median, "#5c940d"), this.inst("piers", piers, "#868e96"), this.inst("crosswalks", cross, "#f8f9fa"),
    ].filter((x): x is GeometryInstance => !!x);
    if (extra.length) this.roadPrims.push(this.prim(extra));
    // frame the town
    const xs = sc.lanes.flatMap((l) => l.centreline.map((p) => p[0]));
    const ys = sc.lanes.flatMap((l) => l.centreline.map((p) => p[1]));
    if (xs.length) {
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
      const r = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) / 2;
      this.viewer.camera.flyToBoundingSphere(new BoundingSphere(this.frame.carla(cx, cy, 0), Math.max(r, 30)), { duration: 0 });
    }
    this.applyLaneColors();
  }

  private applyLaneColors() {
    const lanePrim = this.lanePrim, scene = this.scene;
    if (!lanePrim || !scene || this.disposed) return;
    // per-instance attributes exist only after the primitive's first update (first rendered frame);
    // calling earlier throws "must call update before calling getGeometryInstanceAttributes"
    try {
      lanePrim.getGeometryInstanceAttributes(scene.lanes[0]?.id);
    } catch {
      const remove = this.viewer.scene.postRender.addEventListener(() => {
        remove();
        this.applyLaneColors();
      });
      return;
    }
    const heat = this.checked("heat");
    const counts = new Map<string, number>();
    if (heat) for (const e of this.events) if (e.lane_id) counts.set(e.lane_id, (counts.get(e.lane_id) ?? 0) + 1);
    const max = Math.max(1, ...counts.values());
    for (const l of scene.lanes) {
      const a = lanePrim.getGeometryInstanceAttributes(l.id);
      if (!a) continue;
      let c = Color.fromCssColorString(this.laneColor(l));
      if (heat) {
        const n = counts.get(l.id) ?? 0;
        c = n ? Color.lerp(Color.fromCssColorString("#ffd43b"), Color.fromCssColorString("#c92a2a"), n / max, new Color()) : Color.fromCssColorString("#5c5f66");
      }
      if (this.selectedLane?.id === l.id) c = Color.fromCssColorString("#15aabf");
      a.color = ColorGeometryInstanceAttribute.toValue(c);
    }
  }

  // ------------------------------------------------------------------ town objects (buildings, trees, ...)

  private async buildObjects(town: string) {
    if (this.objectPrim) this.viewer.scene.primitives.remove(this.objectPrim);
    this.objectPrim = null;
    let doc;
    try {
      doc = await api.objects(town);
    } catch {
      return; // not exported for this town: roads only
    }
    if (this.disposed || this.scene?.scene !== town) return;
    const instances: GeometryInstance[] = [];
    const dark = !!this.opts.dark;
    for (const o of doc.objects) {
      const label = doc.labels[o.l];
      // CARLA reports negative extents for mirrored meshes (seen: -12.1 m in Town03)
      const [ex, ey, ez] = o.e.map((v) => Math.max(Math.abs(v), 0.05));
      // a curved linear structure (the Town03 rail loop, long walls) is one mesh whose box spans
      // everything it encloses: drawn as a box it becomes a slab over the town, so leave it out
      if (label !== "Buildings" && ex > 30 && ey > 30) continue;
      const [e, n, u] = [o.c[0], -o.c[1], o.c[2]]; // CARLA -> ENU (north = -y)
      // CARLA yaw is clockwise seen from above (left-handed); ENU rotation is counter-clockwise
      const rot = Matrix3.fromRotationZ((-o.r[1] * Math.PI) / 180);
      const model = Matrix4.multiply(this.frame.toWorld, Matrix4.fromRotationTranslation(rot, new Cartesian3(e, n, u)), new Matrix4());
      const geometry =
        label === "Vegetation"
          ? new EllipsoidGeometry({ radii: new Cartesian3(Math.max(ex, 0.3), Math.max(ey, 0.3), Math.max(ez, 0.3)), vertexFormat: PerInstanceColorAppearance.VERTEX_FORMAT, stackPartitions: 8, slicePartitions: 8 })
          : BoxGeometry.fromDimensions({ dimensions: new Cartesian3(2 * ex, 2 * ey, 2 * ez), vertexFormat: PerInstanceColorAppearance.VERTEX_FORMAT });
      let c = Color.fromCssColorString(OBJECT_COLOR[label] ?? "#868e96");
      if (dark && label === "Buildings") c = Color.fromCssColorString("#7d828a");
      instances.push(new GeometryInstance({ geometry, modelMatrix: model, id: { kind: "object", label, o }, attributes: { color: ColorGeometryInstanceAttribute.fromColor(c) } }));
    }
    const p: Primitive = this.viewer.scene.primitives.add(new Primitive({ geometryInstances: instances, appearance: new PerInstanceColorAppearance({ translucent: false }) }));
    p.show = this.checked("objects");
    this.objectPrim = p;
  }

  // ------------------------------------------------------------------ zones (/api/zones)

  private async loadZones() {
    const name = this.scene?.scene;
    if (!name) return;
    let feats: ZoneFeature[] = [];
    try {
      feats = await api.zones(name, { withStatic: true });
    } catch {
      feats = []; // unknown scene / older backend: no zones layer
    }
    if (this.disposed || this.scene?.scene !== name) return;
    // the scene file's crosswalks are already drawn as road paint
    this.apiZones = feats.filter((f) => !(f.properties.source !== "api" && f.properties.type === "crosswalk"));
    this.drawZones();
  }

  private drawZones() {
    this.zoneLines.removeAll();
    this.zoneLabels.removeAll();
    if (this.zoneFill) this.viewer.scene.primitives.remove(this.zoneFill);
    this.zoneFill = null;
    const n = this.apiZones.filter((f) => f.properties.source === "api").length;
    this.$("zoneCount").textContent = `${n} API zone${n === 1 ? "" : "s"}${this.apiZones.length > n ? `, ${this.apiZones.length - n} from the map file` : ""}`;
    if (!this.checked("zonesOn")) return;
    const fills: GeometryInstance[] = [];
    for (const f of this.apiZones) {
      const ring = openRing(f.geometry.coordinates[0] ?? []);
      if (ring.length < 3) continue;
      const css = zoneColor(String(f.properties.type));
      const [cx, cy] = ring.reduce(([a, b], [x, y]) => [a + x / ring.length, b + y / ring.length], [0, 0]);
      const zz = this.z(cx, cy);
      const id = { kind: "zone", f };
      const selected = this.selectedZone?.id === f.id;
      this.zoneLines.add({
        positions: [...ring, ring[0]].map(([x, y]) => this.frame.carla(x, y, zz + 0.6)), width: selected ? 5 : 3, id,
        material: Material.fromType("Color", { color: Color.fromCssColorString(selected ? "#15aabf" : css) }),
      });
      const fi = this.inst(id, polygonFan(ring, zz, 0.4), css, f.properties.source === "api" ? 0.35 : 0.18);
      if (fi) fills.push(fi);
      this.zoneLabels.add({
        position: this.frame.carla(cx, cy, zz + 3), text: f.properties.name || String(f.properties.type), font: "bold 11px sans-serif",
        disableDepthTestDistance: Number.POSITIVE_INFINITY, style: LabelStyle.FILL_AND_OUTLINE, outlineWidth: 3,
        outlineColor: Color.BLACK, fillColor: Color.fromCssColorString(css),
      });
    }
    if (fills.length) this.zoneFill = this.prim(fills, true);
  }

  // ------------------------------------------------------------------ vehicles, trails, pins

  private drawVehicles(states: VehicleState[]) {
    this.lastDrawn = states.length;
    this.points.removeAll();
    this.labels.removeAll();
    this.trails.removeAll();
    const { frame, z } = this;
    for (const v of states) {
      const pos = frame.carla(v.x, v.y, z(v.x, v.y) + 1.2);
      this.points.add({ position: pos, pixelSize: 10, disableDepthTestDistance: Number.POSITIVE_INFINITY, color: STATE_COLOR[v.state ?? "ok"], outlineColor: Color.BLACK, outlineWidth: 1, id: { kind: "vehicle", v } });
      if (v.speed_kmh != null)
        this.labels.add({ position: pos, text: `${Math.round(v.speed_kmh)}`, font: "11px sans-serif", disableDepthTestDistance: Number.POSITIVE_INFINITY, pixelOffset: new Cartesian2(8, -8),
          style: LabelStyle.FILL_AND_OUTLINE, outlineWidth: 2, outlineColor: Color.BLACK, fillColor: Color.WHITE });
      const trailColor = Material.fromType("Color", { color: STATE_COLOR[v.state ?? "ok"].withAlpha(0.6) });
      if (this.mode === "live") {
        const ps = this.liveTrails.get(v.track_id).map(([x, y]) => frame.carla(x, y, z(x, y) + 1.0));
        if (ps.length > 1) this.trails.add({ positions: ps, width: 2, material: trailColor });
      } else {
        const tr = this.tracks.find((k) => k.id === String(v.track_id));
        if (!tr) continue;
        const ps: Cartesian3[] = [];
        for (let s = this.t - 3; s <= this.t; s += 0.25) {
          const p = sampleTrack(tr, s);
          if (p) ps.push(frame.carla(p.x, p.y, z(p.x, p.y) + 1.0));
        }
        if (ps.length > 1) this.trails.add({ positions: ps, width: 2, material: trailColor });
      }
    }
  }

  private drawPins(upTo: number | null) {
    this.pins.removeAll();
    this.pinLabels.removeAll();
    const kinds = this.val("pinKind");
    if (kinds === "none") return;
    for (const e of this.events) {
      if (upTo != null && eventTime(e) > upTo) continue;
      if ((kinds === "violation" && isAnomaly(e)) || (kinds === "anomaly" && !isAnomaly(e))) continue;
      const st = pinStyle(e);
      const pos = this.frame.carla(e.x, e.y, this.z(e.x, e.y) + 4);
      this.pins.add({ position: pos, pixelSize: st.size, disableDepthTestDistance: Number.POSITIVE_INFINITY, color: Color.fromCssColorString(st.color),
        outlineColor: isAnomaly(e) ? Color.BLACK : Color.WHITE, outlineWidth: 2, id: { kind: "event", e } });
      if (st.label)
        this.pinLabels.add({ position: pos, text: st.label, font: "bold 11px sans-serif", disableDepthTestDistance: Number.POSITIVE_INFINITY, pixelOffset: new Cartesian2(9, 4),
          style: LabelStyle.FILL_AND_OUTLINE, outlineWidth: 3, outlineColor: Color.BLACK, fillColor: Color.fromCssColorString(st.color) });
    }
  }

  // ------------------------------------------------------------------ hotspots and problem sections

  private drawHotspots() {
    if (this.hotPrim) this.viewer.scene.primitives.remove(this.hotPrim);
    this.hotPrim = null;
    this.hotLabels.removeAll();
    const list = gridHotspots(this.events);
    this.$("hotList").innerHTML = list.length
      ? list.map((h, i) => `<li data-x="${h.x}" data-y="${h.y}">#${i + 1} · ${h.count} events · ${Object.entries(h.by_type).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${esc(k)} ${n}`).join(", ")}</li>`).join("")
      : "<li class='muted'>no events</li>";
    if (!this.checked("hotspots") || !list.length) return;
    const max = Math.max(...list.map((h) => h.count));
    const { frame, z } = this;
    const instances = list.map((h, i) => {
      const height = 3 + 17 * (h.count / max); // column height ~ event count (visual only)
      const model = Transforms.eastNorthUpToFixedFrame(frame.carla(h.x, h.y, z(h.x, h.y) + height / 2));
      this.hotLabels.add({ position: frame.carla(h.x, h.y, z(h.x, h.y) + height + 2), text: `#${i + 1} (${h.count})`, font: "bold 13px sans-serif",
        disableDepthTestDistance: Number.POSITIVE_INFINITY, style: LabelStyle.FILL_AND_OUTLINE, outlineWidth: 3, outlineColor: Color.BLACK, fillColor: Color.fromCssColorString("#ff8787") });
      return new GeometryInstance({
        geometry: new CylinderGeometry({ length: height, topRadius: HOT_R, bottomRadius: HOT_R, vertexFormat: PerInstanceColorAppearance.VERTEX_FORMAT }),
        modelMatrix: model, id: { kind: "hotspot", h, rank: i + 1 },
        attributes: { color: ColorGeometryInstanceAttribute.fromColor(Color.lerp(Color.fromCssColorString("#ffd43b"), Color.fromCssColorString("#e03131"), h.count / max, new Color()).withAlpha(0.45)) },
      });
    });
    this.hotPrim = this.viewer.scene.primitives.add(new Primitive({ geometryInstances: instances, appearance: new PerInstanceColorAppearance({ translucent: true, closed: true }), asynchronous: false }));
  }

  private drawSections() {
    const list = problemSections(this.events);
    this.$("sections").innerHTML = list.length
      ? list.map((s) => `<li data-lane="${esc(s.lane_id)}">${esc(s.lane_id)} · ${s.count} · ${Object.entries(s.by_type).map(([k, n]) => `${esc(k)} ${n}`).join(", ")}</li>`).join("")
      : "<li class='muted'>no events on lanes</li>";
  }

  private flyTo(x: number, y: number) {
    this.viewer.camera.flyToBoundingSphere(new BoundingSphere(this.frame.carla(x, y, this.z(x, y)), 60), { duration: 0.8 });
  }

  private refreshEventLayers() {
    this.drawHotspots();
    this.drawSections();
    this.applyLaneColors();
  }

  private render() {
    if (this.disposed) return;
    if (this.mode === "replay") {
      this.drawVehicles(statesAt(this.tracks, this.t, this.evIdx));
      this.drawCustom();
      this.drawPins(this.t);
      const { t, tMin, tMax } = this;
      this.$("clock").textContent = `t = ${t.toFixed(1)} s (${(t - tMin).toFixed(0)} / ${(tMax - tMin).toFixed(0)} s) · ${this.tracks.length} tracks, ${this.lastDrawn} cars shown, ${this.events.length} events`;
      (this.$("time") as HTMLInputElement).value = String(t);
    } else {
      this.drawVehicles(this.liveVehicles);
      this.drawPins(null);
    }
  }

  private tick = (now: number) => {
    if (this.disposed) return;
    if (this.playing && this.mode === "replay") {
      this.t = Math.min(this.tMax, this.t + ((now - this.last) / 1000) * Number(this.val("speed")));
      if (this.t >= this.tMax) this.setPlaying(false);
      this.render();
    }
    this.last = now;
    this.raf = requestAnimationFrame(this.tick);
  };

  private setPlaying(p: boolean) {
    this.playing = p;
    this.$("play").textContent = p ? "⏸" : "▶";
  }

  // ------------------------------------------------------------------ data loading

  private async loadTown(town: string) {
    const sc = await api.scene(town);
    if (this.disposed) return;
    this.scene = sc;
    this.buildRoads(sc);
    void this.buildObjects(sc.scene); // in the background: roads show first
    void this.loadZones();
  }

  /** Shows a session (the panel picker, or the page when ?session= changes). */
  async loadSession(id: string, t: number | null = null) {
    const my = ++this.sessionToken;
    if (t != null) this.pendingSeek = t;
    const stale = () => this.disposed || my !== this.sessionToken;
    (this.$("session") as HTMLSelectElement).value = id;
    const s = await api.session(id);
    if (stale()) return;
    const town = s.town ?? s.scene ?? null;
    if (s.source === "video") {
      // a real clip: its site map in its own metres (no town, no CARLA buildings)
      const sc = await api.sessionScene(id);
      if (stale()) return;
      this.scene = sc;
      this.buildRoads(sc);
      if (this.objectPrim) this.viewer.scene.primitives.remove(this.objectPrim);
      this.objectPrim = null;
      void this.loadZones();
    } else if (town && town !== this.scene?.scene) {
      (this.$("town") as HTMLSelectElement).value = town;
      await this.loadTown(town);
    } else if (!town && !this.scene) {
      // live sessions carry no town: the ?town= one (links pass their map), else the picker's
      const pick = this.opts.town ?? this.val("town");
      (this.$("town") as HTMLSelectElement).value = pick;
      await this.loadTown(pick);
    }
    if (stale()) return;
    const events = await api.events(id);
    const tracks = parseTrajectories(await api.trajectories(id));
    if (stale()) return;
    this.events = events;
    this.evIdx = indexEventsByTrack(events);
    this.tracks = tracks;
    [this.tMin, this.tMax] = timeRange(tracks);
    const el = this.$("time") as HTMLInputElement;
    el.min = String(this.tMin);
    el.max = String(this.tMax);
    const want = this.pendingSeek;
    this.pendingSeek = null;
    this.t = want != null && Number.isFinite(want) && want >= this.tMin && want <= this.tMax ? want : this.tMin;
    this.refreshEventLayers();
    if (this.mode === "live") this.connectLive(id);
    this.render();
  }

  /** Jump the replay to t (s); before the session is loaded it is kept for the load. */
  seek(t: number) {
    if (!this.tracks.length) {
      this.pendingSeek = t;
      return;
    }
    this.t = Math.max(this.tMin, Math.min(this.tMax, t));
    if (this.mode === "replay") this.render();
  }

  private connectLive(id: string | null) {
    this.ws?.close();
    this.ws = null;
    this.liveVehicles = [];
    this.liveTrails.clear();
    if (this.mode !== "live" || this.disposed) return;
    this.$("liveStatus").textContent = "connecting…";
    const ws = new WebSocket(api.liveUrl(id));
    this.ws = ws;
    ws.onopen = () => (this.$("liveStatus").textContent = "live");
    ws.onclose = () => {
      if (!this.disposed && this.ws === ws) this.$("liveStatus").textContent = "closed";
    };
    ws.onmessage = (m) => {
      if (this.disposed || this.ws !== ws) return;
      let msg;
      try {
        msg = JSON.parse(m.data as string);
      } catch {
        return;
      }
      if (msg.type === "vehicles") {
        this.liveVehicles = msg.items;
        this.liveTrails.update(this.liveVehicles);
      } else if (msg.type === "event" && !this.events.some((e) => e.event_id === msg.event.event_id)) {
        this.events.push(msg.event);
        this.evIdx = indexEventsByTrack(this.events);
        this.refreshEventLayers();
      }
      this.render();
    };
  }

  private setMode(m: "replay" | "live") {
    this.mode = m;
    this.root.querySelectorAll<HTMLInputElement>("input[name=tw-mode]").forEach((r) => (r.checked = r.value === m));
    this.setPlaying(false);
    this.connectLive(this.val("session") || null);
    if (m === "replay") this.$("liveStatus").textContent = "";
    this.render();
  }

  // ------------------------------------------------------------------ selection and planner edits

  private kv(o: Record<string, unknown>): string {
    return `<table class="kv">${Object.entries(o)
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(Array.isArray(v) ? v.join(", ") : String(v))}</td></tr>`)
      .join("")}</table>`;
  }

  private select(picked: unknown) {
    this.selectedLane = null;
    const prevZone = this.selectedZone;
    this.selectedZone = null;
    const p = picked as { id?: unknown } | undefined;
    const id = p?.id as { kind?: string; v?: VehicleState; e?: TwinEvent; f?: ZoneFeature } | string | undefined;
    const box = this.$("selected");
    this.$("zoneActions").hidden = true;
    this.$("laneEdit").hidden = true; // the editors appear only once something is selected
    this.$("plannerHint").hidden = false;
    this.$("plannerHintText").textContent = "Click a lane or a zone on the map and its editor appears here.";
    if (typeof id === "string" && this.lanesById.has(id)) {
      const l = this.lanesById.get(id)!;
      this.selectedLane = l;
      this.$("laneEdit").hidden = false;
      this.$("plannerHint").hidden = true;
      const { centreline: _c, z: _z, next: _n, ...rest } = l;
      box.innerHTML = this.kv(rest as Record<string, unknown>);
      for (const a of FALLBACK_ATTRIBUTES) setAttrControl(this.$(`attr_${a.key}`), a, laneValue(l as never, a.key as OverrideKey));
    } else if (id && typeof id === "object" && id.kind === "zone" && id.f) {
      const f = id.f;
      this.selectedZone = f;
      const pr = f.properties;
      box.innerHTML = this.kv({ zone: pr.name, type: ZONE_TYPES[pr.type as ZoneType]?.label ?? pr.type, id: pr.id, source: pr.source,
        grace_s: pr.grace_s, limit_kmh: pr.limit_kmh, version: pr.version, area_m2: pr.area_m2?.toFixed(1),
        updated: pr.updated_at ? `${pr.updated_by ?? "?"}, ${localTime(pr.updated_at)}` : undefined });
      if (pr.source === "api") {
        this.$("plannerHint").hidden = true;
        void this.showZoneActions(f);
      } else {
        this.$("plannerHintText").textContent = "This zone comes from the map file and is read-only. Draw a new zone below to add your own.";
      }
    } else if (id && typeof id === "object" && id.kind === "vehicle" && id.v) {
      box.innerHTML = this.kv({ track: id.v.track_id, state: id.v.state, speed_kmh: id.v.speed_kmh?.toFixed(1), heading: id.v.heading_deg?.toFixed(0) });
    } else if (id && typeof id === "object" && id.kind === "object") {
      const ob = id as unknown as { label: string; o: { c: number[]; e: number[]; r: number[] } };
      box.innerHTML = this.kv({ object: ob.label, centre: ob.o.c.map((v) => v.toFixed(1)).join(", "),
        size_m: ob.o.e.map((v) => (2 * v).toFixed(1)).join(" × "), top_m: (ob.o.c[2] + ob.o.e[2]).toFixed(1), yaw: ob.o.r[1] });
    } else if (id && typeof id === "object" && id.kind === "plan") {
      const pid = (id as unknown as { id: string }).id;
      this.builder.selectItem(pid);
      box.textContent = "A plan item: edit it in the scenario builder.";
    } else if (id && typeof id === "object" && id.kind === "event" && id.e) {
      const e = id.e;
      box.innerHTML = isAnomaly(e)
        ? this.kv({ "road surface": e.type, severity: e.severity_score?.toFixed(2), band: e.severity_band, area_m2: e.area_sq_m?.toFixed(2), time_s: eventTime(e).toFixed(1), lane: e.lane_id, status: e.status, confidence: e.confidence?.toFixed(2) })
        : this.kv({ type: e.type, condition: e.condition, tracks: e.track_ids, time_s: eventTime(e).toFixed(1), lane: e.lane_id, status: e.status, value: JSON.stringify(e.value ?? {}) });
    } else if (id && typeof id === "object" && id.kind === "hotspot") {
      const hs = id as unknown as { h: { count: number; by_type: Record<string, number>; lane_ids: string[]; x: number; y: number }; rank: number };
      box.innerHTML = this.kv({ hotspot: `#${hs.rank}`, events: hs.h.count, types: Object.entries(hs.h.by_type).map(([k, n]) => `${k} ${n}`).join(", "), lanes: hs.h.lane_ids, centre: `${hs.h.x.toFixed(0)}, ${hs.h.y.toFixed(0)}` });
    } else {
      box.textContent = "Click a lane, vehicle, pin or zone.";
    }
    if (prevZone !== this.selectedZone) this.drawZones();
    this.applyLaneColors();
  }

  private async showZoneActions(f: ZoneFeature) {
    const box = this.$("zoneActions");
    box.hidden = false;
    (this.$("zoneRedraw") as HTMLButtonElement).hidden = !this.canEditZone();
    (this.$("zoneDeactivate") as HTMLButtonElement).hidden = !can(this.opts.role, "deactivate_zone");
    this.$("zoneHistory").innerHTML = "<li>loading history…</li>";
    try {
      const h = await api.zoneHistory(f.id);
      if (this.selectedZone?.id !== f.id) return;
      this.$("zoneHistory").innerHTML = h.map((r) => `<li>v${r.version} ${esc(r.action)} · ${esc(r.by)} · ${esc(localTime(r.at))}${r.note ? `<br>${esc(r.note)}` : ""}</li>`).join("") || "<li>no history</li>";
    } catch {
      this.$("zoneHistory").innerHTML = "<li>no history</li>";
    }
  }

  private renderPending() {
    this.$("pending").innerHTML =
      this.pendingOverrides.map((o) => `<li>lane ${esc(o.lane_id)}: ${esc(describeItem(o))}</li>`).join("") +
      this.pendingZones.map((zn) => `<li>zone “${esc(zn.name)}” ${esc(zn.type)} (${zn.polygon.length} pts${zn.limit_kmh != null ? `, ${zn.limit_kmh} km/h` : ""}${zn.grace_s != null ? `, grace ${zn.grace_s} s` : ""})</li>`).join("") ||
      "<li class='muted'>none</li>";
    this.$("saveBox").hidden = !this.pendingOverrides.length && !this.pendingZones.length; // nothing queued: nothing to save
  }

  private addOverride() {
    const lane = this.selectedLane, scene = this.scene;
    if (!lane || !scene) {
      this.$("saveErr").textContent = "Click a lane first";
      return;
    }
    const o: LaneOverride = { lane_id: overrideTarget(lane.id, this.checked("roadScope") ? "road_dir" : "lane") };
    const rec = o as unknown as Record<string, unknown>;
    for (const a of FALLBACK_ATTRIBUTES) {
      const v = readAttrControl(this.$(`attr_${a.key}`), a);
      if (v !== undefined && v !== laneValue(lane as never, a.key as OverrideKey)) rec[a.key] = v;
    }
    const errs = validateOverride(o, scene.lanes);
    if (!OVERRIDE_KEYS.some((k) => k in o) && !errs.includes("nothing to change")) errs.push("nothing changed");
    this.$("saveErr").textContent = errs.join("\n");
    if (!errs.length) {
      this.pendingOverrides.push(o);
      this.renderPending();
    }
  }

  private updateZoneParam() {
    const type = this.val("zoneType");
    const param = ZONE_TYPES[type as ZoneType]?.param ?? null;
    this.$("zoneParamRow").hidden = !param;
        this.$("zoneParamLabel").textContent = param === "limit_kmh" ? "Limit (km/h, required)" : "Grace (s, optional; short = no stopping)";
  }

  private drawPreview() {
    this.drawn.removeAll();
    const poly = this.drawing ?? [];
    if (poly.length > 1)
      this.drawn.add({ positions: [...poly, poly[0]].map(([x, y]) => this.frame.carla(x, y, this.z(x, y) + 0.5)), width: 3,
        material: Material.fromType("Color", { color: Color.ORANGE }) });
  }

  /** Right-click: the drawn outline becomes a what-if area, a pending zone, or a zone's new outline. */
  private async finishDrawing() {
    const poly = this.drawing;
    if (!poly) return;
    this.drawing = null;
    this.$("drawHint").textContent = "";
    this.drawPreview();
    const err = this.$("saveErr");
    if (this.redrawZone) {
      const f = this.redrawZone;
      this.redrawZone = null;
      const problem = polygonProblem(poly);
      if (problem) {
        err.textContent = `Zone outline: ${problem}`;
        return;
      }
      const updated = await api.updateZone(f.id, { polygon: poly, note: this.val("note").trim() || "outline redrawn in the 3D twin" });
      err.innerHTML = `<span class="ok">Zone ${esc(updated.properties.name)} is now v${updated.properties.version}.</span>`;
      await this.loadZones();
      this.select({ id: { kind: "zone", f: this.apiZones.find((z) => z.id === f.id) ?? updated } });
      return;
    }
    const type = this.val("zoneType");
    const info = ZONE_TYPES[type as ZoneType];
    const errs: string[] = [];
    const problem = polygonProblem(poly);
    if (problem) errs.push(`Zone outline: ${problem}`);
    const name = this.val("zoneName").trim() || `${info?.label ?? type} (twin)`;
    const raw = this.val("zoneParam").trim();
    const zn: PendingZone = { id: `twin_${Date.now()}`, type, name, polygon: poly };
    if (info?.param === "limit_kmh") {
      const n = Number(raw);
      if (!raw || !Number.isFinite(n) || n < 1 || n > 200) errs.push("Speed zone: limit 1-200 km/h is required");
      else zn.limit_kmh = n;
    } else if (info?.param === "grace_s" && raw) {
      const n = Number(raw);
      if (!Number.isFinite(n) || n < 0) errs.push("Grace must be a number of seconds ≥ 0");
      else zn.grace_s = n;
    }
    err.textContent = errs.join("\n");
    if (!errs.length) {
      this.pendingZones.push(zn);
      this.renderPending();
    }
  }

  private async save() {
    const name = this.val("profileName").trim() || (this.$("profileName") as HTMLInputElement).placeholder;
    const note = this.val("note").trim();
    const err = this.$("saveErr");
    const errs = [validateNote(note)].filter(Boolean) as string[];
    if (this.pendingOverrides.length) {
      const e = validateProfileName(name);
      if (e) errs.push(e);
    }
    if (!this.pendingOverrides.length && !this.pendingZones.length) errs.push("no pending edits");
    if (this.pendingZones.length && !this.scene) errs.push("no map loaded");
    err.textContent = errs.join("\n");
    if (errs.length) return;
    const done: string[] = [];
    try {
      if (this.pendingOverrides.length) {
        let base: Record<string, any>;
        try {
          base = unwrapProfile(await api.profile(name));
        } catch {
          base = { ...unwrapProfile(await api.profile("default")), description: `Planner edits on ${this.scene?.scene}`, applies_to: { map: this.scene?.scene ?? null } };
        }
        const profile = buildProfile(base, name, this.pendingOverrides);
        const res = await api.putProfile(name, profile, note);
        this.pendingOverrides.length = 0;
        const file = res?.file ?? `backend/data/profiles/${name}.json`;
        done.push(`Lane changes saved as ${esc(name)} v${res?.version ?? "?"}. Engine: run_violations.py &lt;flight&gt; --profile ${esc(file)}`);
        void this.loadHistory(name);
      }
      let zoneFile: string | null | undefined;
      while (this.pendingZones.length) {
        const zn = this.pendingZones[0];
        const f = await api.createZone({ scene: this.scene!.scene, name: zn.name, type: zn.type, polygon: zn.polygon,
          ...(zn.grace_s != null ? { grace_s: zn.grace_s } : {}), ...(zn.limit_kmh != null ? { limit_kmh: zn.limit_kmh } : {}), note });
        this.pendingZones.shift();
        zoneFile = f.file ?? zoneFile;
        done.push(`Zone “${esc(f.properties.name)}” created (${esc(f.id)}).`);
      }
      if (zoneFile !== undefined) {
        done.push(`Engine zones file: ${esc(zoneFile ?? `backend/data/zones/${this.scene?.scene}.json`)}`);
        void this.loadZones();
      }
      err.innerHTML = `<span class="ok">${done.join("<br>")}</span>`;
    } catch (e) {
      err.innerHTML = `${done.length ? `<span class="ok">${done.join("<br>")}</span><br>` : ""}${esc((e as Error).message)}`;
    } finally {
      this.renderPending();
    }
  }

  private async deactivateSelectedZone() {
    const f = this.selectedZone;
    if (!f) return;
    if (!window.confirm(`Deactivate zone “${f.properties.name}”? It stays in the history; an ADMIN can reactivate it.`)) return;
    await api.deactivateZone(f.id, this.val("note").trim() || undefined);
    this.$("saveErr").innerHTML = `<span class="ok">Zone ${esc(f.properties.name)} deactivated.</span>`;
    this.select(undefined);
    await this.loadZones();
  }

  private async loadHistory(name: string) {
    try {
      const h = await api.profileHistory(name);
      this.$("history").innerHTML = h.map((r: any) => `<li>v${r.version ?? "?"} · ${esc(r.by ?? "?")} · ${esc(r.at ? localTime(r.at) : "")}<br>${esc(r.note ?? "")}</li>`).join("") || "<li>none</li>";
    } catch {
      this.$("history").innerHTML = "<li>none yet</li>";
    }
  }

  // ------------------------------------------------------------------ scenario builder (right-click menu, plan markers)

  /** Show the vehicle paths of a virtual-traffic run as the custom traffic layer (null: remove it). */
  private showVirtual(traj: Record<string, [number, number, number, number | null][]> | null) {
    if (this.disposed) return;
    const sw = this.$("custom") as HTMLInputElement;
    if (traj) {
      const tracks = parseTrajectories(traj);
      const [a, b] = timeRange(tracks);
      const ownClock = !this.tracks.length; // nothing recorded: the simulation sets the timeline
      this.custom = { tracks, t0: a, dur: Math.max(b - a, 1), ownClock };
      if (this.mode !== "replay") this.setMode("replay");
      if (ownClock) {
        this.tMin = a;
        this.tMax = b;
        const el = this.$("time") as HTMLInputElement;
        el.min = String(a);
        el.max = String(b);
      }
      this.t = this.tMin;
      sw.disabled = false;
      sw.checked = true;
      this.$("customNote").textContent = `(${tracks.length} vehicles, ${this.builder?.hasItems ? "following your plan" : "plain traffic"})`;
      this.render();
      this.setPlaying(true);
    } else if (this.custom) {
      if (this.custom.ownClock) {
        this.tMin = this.tMax = this.t = 0;
        this.setPlaying(false);
      }
      this.custom = null;
      sw.checked = false;
      this.$("customNote").textContent = "(off)";
      this.drawCustom();
      this.render();
    }
  }

  private lastTraffic: import("../api/types").TrafficResult | null = null;
  private customSeq = 0;
  private customTimer: ReturnType<typeof setTimeout> | undefined;

  /** The Custom traffic switch (info panel or the builder's bottom bar). */
  private setCustomTraffic(on: boolean) {
    const sw = this.$("custom") as HTMLInputElement;
    sw.checked = on;
    if (on) void this.guard(this.loadCustomTraffic());
    else {
      this.customSeq++;
      this.$("customNote").textContent = "(off)";
      this.drawCustom();
    }
  }

  /** Virtual traffic from the server: plain, or following the plan in the builder; re-run when the plan changes while the switch is on. */
  private async loadCustomTraffic() {
    const note = this.$("customNote");
    const town = this.scene?.scene;
    if (!town) {
      note.textContent = "(load a map first)";
      (this.$("custom") as HTMLInputElement).checked = false;
      return;
    }
    const plan = this.builder.planForTraffic();
    const seq = ++this.customSeq;
    note.textContent = this.builder.hasItems ? "(simulating your plan…)" : "(simulating the traffic…)";
    try {
      const r = await api.planTraffic(town, plan);
      if (this.disposed || seq !== this.customSeq || !this.checked("custom")) return;
      const keepT = this.custom ? this.t : null;
      this.lastTraffic = r;
      this.showVirtual(r.trajectories);
      if (keepT !== null) this.t = keepT; // a re-run for a changed plan keeps the clock where it was
      this.$("customNote").textContent = `(${r.vehicles} vehicles${r.follows_plan ? `, following your plan: ${r.plan_items} item${r.plan_items === 1 ? "" : "s"}` : ""}, ${r.description ?? "plain traffic"})`;
      this.builder.refreshTrafficInfo();
    } catch (e) {
      if (seq === this.customSeq) {
        this.$("customNote").textContent = `(${(e as Error).message})`;
        (this.$("custom") as HTMLInputElement).checked = false;
      }
    }
  }

  /** The plan in the builder changed: with the switch on, the traffic follows it. */
  private onPlanChanged() {
    if (!this.checked("custom")) return;
    clearTimeout(this.customTimer);
    this.customTimer = setTimeout(() => void this.guard(this.loadCustomTraffic()), 900);
  }

  /** Custom traffic markers (blue) at the replay clock, looped over the simulation's length; hidden when the switch is off. */
  private drawCustom() {
    this.customPoints.removeAll();
    this.customLabels.removeAll();
    const c = this.custom;
    if (!c || !this.checked("custom")) return;
    const tt = c.t0 + ((((this.t - this.tMin) % c.dur) + c.dur) % c.dur);
    for (const tr of c.tracks) {
      const st = sampleTrack(tr, tt);
      if (!st) continue;
      const pos = this.frame.carla(st.x, st.y, this.z(st.x, st.y) + 1.2);
      const stopped = (st.speed_kmh ?? 0) < 1;
      this.customPoints.add({ position: pos, pixelSize: 10, color: Color.fromCssColorString(stopped ? "#f08c00" : "#339af0"), outlineColor: Color.WHITE, outlineWidth: 1,
        disableDepthTestDistance: Number.POSITIVE_INFINITY });
      if (st.speed_kmh != null && !stopped)
        this.customLabels.add({ position: pos, text: `${Math.round(st.speed_kmh)}`, font: "11px sans-serif", disableDepthTestDistance: Number.POSITIVE_INFINITY, pixelOffset: new Cartesian2(8, -8),
          style: LabelStyle.FILL_AND_OUTLINE, outlineWidth: 3, outlineColor: Color.BLACK, fillColor: Color.fromCssColorString("#a5d8ff") });
    }
  }

  private onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") this.closeContextMenu();
  };

  private closeContextMenu() {
    this.ctxMenu?.remove();
    this.ctxMenu = null;
  }

  /** Right-click on the map: what is there, and "Add Custom Rules" (opens the scenario builder at that position). */
  private openContextMenu(pos: Cartesian2) {
    this.closeContextMenu();
    const p = this.viewer.camera.pickEllipsoid(pos);
    if (!p) return;
    const [x0, y0] = this.frame.toCarla(p);
    const x = Math.round(x0 * 100) / 100, y = Math.round(y0 * 100) / 100;
    const canPlan = this.opts.role === "PLANNER" || this.opts.role === "ADMIN";
    const m = document.createElement("div");
    m.className = "tw-ctx";
    const rootBox = this.root.getBoundingClientRect(), vb = (this.viewer.canvas as HTMLCanvasElement).getBoundingClientRect();
    m.style.left = `${Math.min(pos.x + vb.left - rootBox.left, rootBox.width - 250)}px`;
    m.style.top = `${Math.min(pos.y + vb.top - rootBox.top, rootBox.height - 150)}px`;
    m.innerHTML = `<div class="ctx-h"><b>${x.toFixed(1)}, ${y.toFixed(1)}</b><div id="ctxWhere" class="muted">looking up the road…</div></div>
      <button data-ctx="open" ${canPlan ? "" : "disabled"}>Add Custom Rules</button>
      ${this.builder.isOpen ? `<button data-ctx="move" ${canPlan ? "" : "disabled"}>Set builder location here</button>` : ""}
      ${canPlan ? "" : "<div class='ctx-note'>PLANNER role needed to build scenarios.</div>"}`;
    m.addEventListener("click", (e) => {
      const act = (e.target as HTMLElement).closest<HTMLElement>("[data-ctx]")?.dataset.ctx;
      if (!act) return;
      this.closeContextMenu();
      void this.guard(this.builder.openAt(x, y));
    });
    this.root.appendChild(m);
    this.ctxMenu = m;
    void api.planLocate(this.scene?.scene ?? "", x, y).then((loc) => {
      const el = m.querySelector<HTMLElement>("#ctxWhere");
      if (!el || this.ctxMenu !== m) return;
      const n = loc.nearest_lane;
      el.textContent = loc.on_road && n ? `lane ${n.id} · ${n.lane_type ?? "road"}${n.speed_limit_kmh ? ` · ${n.speed_limit_kmh} km/h` : ""}${loc.junction ? " · junction" : ""}` : "not on a road";
    }).catch(() => {
      const el = m.querySelector<HTMLElement>("#ctxWhere");
      if (el) el.textContent = "";
    });
  }

  /** The builder's items on the map: a marker + label per item, footprints for area items, a cross at the builder location. */
  private drawPlan(items: DrawnItem[], anchor: [number, number] | null) {
    this.planPoints.removeAll();
    this.planLabels.removeAll();
    this.planLines.removeAll();
    const colour = { prop: "#4c6ef5", rule: "#f08c00", scenario: "#12b886" } as const;
    for (const it of items) {
      const c = Color.fromCssColorString(it.level === "error" ? "#fa5252" : it.level === "warning" ? "#fab005" : colour[it.group]);
      const pos = this.frame.carla(it.x, it.y, this.z(it.x, it.y) + 1.5);
      this.planPoints.add({ position: pos, pixelSize: it.selected ? 17 : 12, color: c, outlineColor: it.selected ? Color.WHITE : Color.BLACK,
        outlineWidth: it.selected ? 3 : 1, disableDepthTestDistance: Number.POSITIVE_INFINITY, id: { kind: "plan", id: it.id } });
      this.planLabels.add({ position: pos, text: it.label, font: "bold 12px sans-serif", disableDepthTestDistance: Number.POSITIVE_INFINITY, pixelOffset: new Cartesian2(11, -14),
        style: LabelStyle.FILL_AND_OUTLINE, outlineWidth: 3, outlineColor: Color.BLACK, fillColor: Color.WHITE });
      if (it.footprint) {
        const ring = [...it.footprint, it.footprint[0]].map(([x, y]) => this.frame.carla(x, y, this.z(x, y) + 0.6));
        this.planLines.add({ positions: ring, width: it.selected ? 4 : 2, material: Material.fromType("Color", { color: c }) });
      }
    }
    if (anchor) {
      const pos = this.frame.carla(anchor[0], anchor[1], this.z(anchor[0], anchor[1]) + 1.2);
      this.planPoints.add({ position: pos, pixelSize: 9, color: Color.WHITE, outlineColor: Color.fromCssColorString("#4c6ef5"), outlineWidth: 3, disableDepthTestDistance: Number.POSITIVE_INFINITY });
    }
  }

  // ------------------------------------------------------------------ wiring

  private wire() {
    const { viewer, handler } = this;
    handler.setInputAction((ev: { position: Cartesian2 }) => {
      if (this.drawing) {
        const p = viewer.camera.pickEllipsoid(ev.position);
        if (p) {
          const [x, y] = this.frame.toCarla(p);
          this.drawing.push([Math.round(x * 100) / 100, Math.round(y * 100) / 100]);
          this.drawPreview();
        }
        return;
      }
      this.closeContextMenu();
      this.select(viewer.scene.pick(ev.position));
    }, ScreenSpaceEventType.LEFT_CLICK);
    handler.setInputAction((ev: { position: Cartesian2 }) => {
      if (this.drawing || this.redrawZone) return void this.guard(this.finishDrawing());
      this.openContextMenu(ev.position);
    }, ScreenSpaceEventType.RIGHT_CLICK);
    this.root.addEventListener("pointerdown", (e) => { if (this.ctxMenu && !this.ctxMenu.contains(e.target as Node)) this.closeContextMenu(); });
    window.addEventListener("keydown", this.onKey);

    const on = (id: string, ev: string, fn: (e: Event) => unknown) =>
      this.$(id).addEventListener(ev, (e) => void this.guard(Promise.resolve().then(() => fn(e))));
    on("drawZone", "click", () => {
      this.redrawZone = null;
      this.drawing = [];
      this.$("drawHint").textContent = "left-click corners, right-click to finish";
    });
    on("zoneRedraw", "click", () => {
      if (!this.selectedZone) return;
      this.redrawZone = this.selectedZone;
      this.drawing = [];
      this.$("drawHint").textContent = `new outline for “${this.selectedZone.properties.name}”: left-click corners, right-click to finish`;
      this.$("saveErr").textContent = "";
    });
    on("zoneDeactivate", "click", () => this.deactivateSelectedZone());
    on("zoneType", "change", () => this.updateZoneParam());
    on("addOverride", "click", () => this.addOverride());
    on("save", "click", () => this.save());
    on("play", "click", () => this.setPlaying(!this.playing));
    on("time", "input", (e) => {
      this.t = Number((e.target as HTMLInputElement).value);
      this.render();
    });
    on("heat", "change", () => this.applyLaneColors());
    on("custom", "change", () => this.setCustomTraffic(this.checked("custom")));
    this.$("customCfg").addEventListener("click", (e) => {
      e.preventDefault(); // the gear sits inside the switch's label: it must not flip the switch
      e.stopPropagation();
      void this.guard(this.builder.openTraffic());
    });
    on("traffic", "change", () => {
      const show = this.checked("traffic");
      this.points.show = this.labels.show = this.trails.show = show;
      this.root.classList.toggle("traffic-off", !show); // replay / live controls, clock and vehicle-state legend only matter with traffic
    });
    on("hotspots", "change", () => this.drawHotspots());
    on("zonesOn", "change", () => this.drawZones());
    on("pinKind", "change", () => this.render());
    on("hotList", "click", (e) => {
      const li = (e.target as HTMLElement).closest("li");
      if (li?.dataset.x) this.flyTo(Number(li.dataset.x), Number(li.dataset.y));
    });
    on("sections", "click", (e) => {
      const lane = this.lanesById.get((e.target as HTMLElement).closest("li")?.dataset.lane ?? "");
      if (!lane) return;
      this.select({ id: lane.id });
      const mid = lane.centreline[Math.floor(lane.centreline.length / 2)];
      this.flyTo(mid[0], mid[1]);
    });
    on("panelToggle", "click", () => this.root.classList.toggle("collapsed"));
    on("plannerToggle", "click", () => this.root.classList.toggle("planner-collapsed"));
    on("objects", "change", () => {
      if (this.objectPrim) this.objectPrim.show = this.checked("objects");
    });
    on("session", "change", async (e) => {
      const id = (e.target as HTMLSelectElement).value;
      this.opts.onSession?.(id);
      await this.loadSession(id);
    });
    on("town", "change", (e) => this.loadTown((e.target as HTMLSelectElement).value));
    this.root.querySelectorAll<HTMLInputElement>("input[name=tw-mode]").forEach((r) =>
      r.addEventListener("change", () => this.setMode(r.value as "replay" | "live")),
    );
  }

  private async start() {
    const o = this.opts;
    this.$("user").textContent = `${o.username} (${o.role})`;
    const editor = this.canEditProfile() || this.canEditZone();
    this.$("editor").hidden = !editor;
    this.$("plannerToggle").hidden = !editor;
    this.updateZoneParam();
    this.renderPending();
    if (o.t != null && Number.isFinite(o.t)) this.pendingSeek = o.t;
    this.raf = requestAnimationFrame(this.tick);
    const scenes = await api.scenes();
    if (this.disposed) return;
    const towns = scenes.map((s) => (typeof s === "string" ? s : String((s as any).town ?? (s as any).scene ?? (s as any).name))).map((s) => s.replace(/\.json$/, ""));
    this.$("town").innerHTML = towns.map((s) => `<option>${esc(s)}</option>`).join("");
    const sessions = await api.sessions();
    if (this.disposed) return;
    this.$("session").innerHTML = sessions.map((s) => `<option value="${esc(s.session_id)}">${esc(s.name ?? s.session_id)}</option>`).join("");
    const want = o.session ?? sessions[0]?.session_id;
    const town = o.town ?? towns[0];
    if (o.town && towns.includes(o.town)) (this.$("town") as HTMLSelectElement).value = o.town;
    if (want) {
      if (!o.session) o.onSession?.(want);
      await this.loadSession(want);
    } else if (town) await this.loadTown(town);
    if (this.disposed) return;
    if ((o.mode ?? "replay") !== this.mode) this.setMode(o.mode ?? "replay");
    if (this.canEditProfile()) {
      const profiles = profileSummaries(await api.profiles().catch(() => []));
      (this.$("profileName") as HTMLInputElement).placeholder =
        profiles.find((p) => p.name.includes("planner"))?.name ?? `${(this.scene?.scene ?? "town").toLowerCase()}_planner`;
    }
  }

  /** Stops the loop, closes the socket and frees the WebGL context. */
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    window.removeEventListener("keydown", this.onKey);
    clearTimeout(this.customTimer);
    this.closeContextMenu();
    this.builder?.dispose();
    this.ws?.close();
    this.ws = null;
    if (!this.handler.isDestroyed()) this.handler.destroy();
    if (!this.viewer.isDestroyed()) this.viewer.destroy();
    this.root.innerHTML = "";
    this.root.classList.remove("twin-root", "collapsed");
  }
}
