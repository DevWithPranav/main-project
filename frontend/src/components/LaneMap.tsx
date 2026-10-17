// Top-down 2D map in map metres (CARLA x right, y down, like the drone camera): lane centrelines,
// zones, vehicles, event pins and an optional heat layer. Wheel to zoom, drag to pan; an `editor`
// gets clicks and drags in map metres (the zone drawer).

import { useComputedColorScheme } from '@mantine/core';
import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react';
import type { Lane, Zone } from '../api/types';

export interface MapPoint {
  id: string | number;
  x: number;
  y: number;
  color: string;
  r?: number;
  label?: string;
  heading_deg?: number;
  /** pins: circle (violation) or diamond (road-surface anomaly) */
  shape?: 'circle' | 'diamond';
}

export interface Trail {
  id: string | number;
  points: [number, number][];
  color: string;
}

/** Hotspot ring: centre, radius in metres, rank label. */
export interface Ring {
  id: string | number;
  x: number;
  y: number;
  r: number;
  label: string;
  title?: string;
}

export interface HeatCell {
  x: number;
  y: number;
  size: number;
  n: number;
}

/** Map-metre pointer callbacks for drawing / editing on the map (zone drawer). While `capture` is
 * true a drag goes to onMove instead of panning. A click is a press and release within 4 px. */
export interface MapEditor {
  onClick?: (x: number, y: number) => void;
  onDoubleClick?: (x: number, y: number) => void;
  onMove?: (x: number, y: number) => void;
  onUp?: () => void;
  capture?: boolean;
  cursor?: string;
}

interface Props {
  lanes?: Lane[];
  zones?: Zone[];
  /** zone fill colour; default amber */
  zoneColor?: (z: Zone) => string;
  selectedZone?: string | null;
  onZoneClick?: (z: Zone) => void;
  editor?: MapEditor;
  vehicles?: MapPoint[];
  pins?: MapPoint[];
  heat?: HeatCell[];
  trails?: Trail[];
  rings?: Ring[];
  laneColor?: (l: Lane) => string | null;
  selectedLane?: string | null;
  /** several selected lanes (drawn with a halo under their own colour) */
  selectedLanes?: Set<string>;
  /** lanes to mark (dashed outline), e.g. the ones a profile overrides */
  markedLanes?: Set<string>;
  /** the click event comes along for modifier keys (shift-click to add) */
  onLaneClick?: (l: Lane, e: ReactMouseEvent) => void;
  onPinClick?: (id: string | number) => void;
  height?: number | string;
  /** extra SVG in map metres; a function gets the size of one screen pixel in metres */
  children?: ReactNode | ((unit: number) => ReactNode);
}

type Box = [number, number, number, number]; // x, y, w, h

function bounds(lanes: Lane[] | undefined, pts: MapPoint[]): Box {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const add = (x: number, y: number) => {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  };
  lanes?.forEach((l) => l.centreline.forEach(([x, y]) => add(x, y)));
  if (!isFinite(x0)) pts.forEach((p) => add(p.x, p.y));
  if (!isFinite(x0)) return [-100, -100, 200, 200];
  const pad = 10;
  return [x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad];
}

export default function LaneMap({
  lanes, zones, zoneColor, selectedZone, onZoneClick, editor, vehicles = [], pins = [], heat, trails, rings, laneColor, selectedLane,
  selectedLanes, markedLanes, onLaneClick, onPinClick, height = 520, children,
}: Props) {
  const dark = useComputedColorScheme('light') === 'dark';
  const full = useMemo(() => bounds(lanes, [...vehicles, ...pins]), [lanes]); // eslint-disable-line react-hooks/exhaustive-deps
  const [view, setView] = useState<Box>(full);
  // refit only when the extent changes (a recoloured / previewed copy of the same lanes keeps the zoom)
  useEffect(() => setView(full), [full.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ x: number; y: number; v: Box; moved: boolean } | null>(null);
  const lastClick = useRef<{ x: number; y: number; t: number } | null>(null);

  const toMap = (cx: number, cy: number, v: Box): [number, number] => {
    const r = svg.current!.getBoundingClientRect();
    const s = Math.max(v[2] / r.width, v[3] / r.height); // preserveAspectRatio meet
    const ox = v[0] + v[2] / 2 - (r.width * s) / 2;
    const oy = v[1] + v[3] / 2 - (r.height * s) / 2;
    return [ox + (cx - r.left) * s, oy + (cy - r.top) * s];
  };

  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setView((v) => {
        const [mx, my] = toMap(e.clientX, e.clientY, v);
        const k = e.deltaY > 0 ? 1.2 : 1 / 1.2;
        return [mx - (mx - v[0]) * k, my - (my - v[1]) * k, v[2] * k, v[3] * k];
      });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  const unit = Math.max(view[2], view[3]) / 600; // ~1 screen px in map metres
  // a 4.6 m car is ~5 px over a whole town: draw cars at least ~11 px long, true size when zoomed in
  const carScale = Math.max(1, (unit * 11) / 4.6);
  const base = dark ? '#5c5f66' : '#adb5bd';
  const lineFor = (l: Lane) => {
    const c = laneColor?.(l);
    if (c) return c;
    if (l.bridge) return dark ? '#9775fa' : '#7048e8';
    if (l.junction) return dark ? '#373a40' : '#dee2e6';
    if (l.lane_type && l.lane_type !== 'driving') return dark ? '#2c2e33' : '#e9ecef';
    return base;
  };

  return (
    <svg
      ref={svg}
      viewBox={view.join(' ')}
      style={{ width: '100%', height, background: dark ? '#1a1b1e' : '#f8f9fa', borderRadius: 8, cursor: editor?.cursor ?? 'grab', touchAction: 'none' }}
      onPointerDown={(e) => {
        drag.current = { x: e.clientX, y: e.clientY, v: view, moved: false };
        (e.target as Element).setPointerCapture?.(e.pointerId); // the target: a lane / pin / zone still gets its click
      }}
      onPointerMove={(e) => {
        if (editor?.onMove && (editor.capture || !drag.current)) editor.onMove(...toMap(e.clientX, e.clientY, view));
        const d = drag.current;
        if (!d) return;
        if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > 4) d.moved = true;
        if (editor?.capture) return;
        const r = svg.current!.getBoundingClientRect();
        const s = Math.max(d.v[2] / r.width, d.v[3] / r.height);
        setView([d.v[0] - (e.clientX - d.x) * s, d.v[1] - (e.clientY - d.y) * s, d.v[2], d.v[3]]);
      }}
      onPointerUp={(e) => {
        const d = drag.current;
        drag.current = null;
        if (d && !d.moved && editor?.onClick && !editor.capture) {
          // the second press of a double click is not another click (it would add the point twice)
          const c = lastClick.current, now = e.timeStamp;
          const second = !!c && now - c.t < 500 && Math.hypot(e.clientX - c.x, e.clientY - c.y) <= 4;
          lastClick.current = second ? null : { x: e.clientX, y: e.clientY, t: now };
          if (!second) editor.onClick(...toMap(e.clientX, e.clientY, view));
        }
        editor?.onUp?.();
      }}
      onDoubleClick={(e) => (editor?.onDoubleClick ? editor.onDoubleClick(...toMap(e.clientX, e.clientY, view)) : setView(full))}
    >
      {heat?.map((h, i) => (
        <rect key={`h${i}`} x={h.x} y={h.y} width={h.size} height={h.size} fill="#fa5252" opacity={Math.min(0.75, 0.15 + h.n * 0.08)} />
      ))}
      {zones?.map((z) => {
        const c = zoneColor?.(z) ?? '#fab005';
        const sel = z.id === selectedZone;
        return (
          <polygon key={z.id} points={z.polygon.map((p) => p.join(',')).join(' ')} fill={c} fillOpacity={sel ? 0.4 : 0.22}
            stroke={sel ? '#228be6' : c} strokeWidth={unit * (sel ? 3 : 1.5)} strokeDasharray={z.source === 'api' ? undefined : `${unit * 4} ${unit * 3}`}
            style={{ cursor: onZoneClick ? 'pointer' : undefined }}
            onClick={onZoneClick ? (e) => { e.stopPropagation(); onZoneClick(z); } : undefined}>
            <title>{`${z.name ?? z.id} · ${z.type}${z.source === 'api' ? '' : ' (from the map file)'}`}</title>
          </polygon>
        );
      })}
      {!!(selectedLanes?.size || markedLanes?.size) && lanes?.map((l) => {
        const sel = selectedLanes?.has(l.id), mark = markedLanes?.has(l.id);
        if (!sel && !mark) return null;
        const pts = l.centreline.map((p) => `${p[0]},${p[1]}`).join(' ');
        const w = Math.max(unit * 1.5, (l.width_m ?? 3) * 0.35);
        return (
          <g key={`halo${l.id}`} pointerEvents="none">
            {sel && <polyline points={pts} fill="none" stroke="#228be6" strokeOpacity={0.55} strokeWidth={w + unit * 7} strokeLinecap="round" strokeLinejoin="round" />}
            {mark && <polyline points={pts} fill="none" stroke={dark ? '#f8f9fa' : '#212529'} strokeWidth={w + unit * 3}
              strokeDasharray={`${unit * 4} ${unit * 3}`} strokeLinejoin="round" />}
          </g>
        );
      })}
      {lanes?.map((l) => (
        <polyline
          key={l.id}
          points={l.centreline.map((p) => `${p[0]},${p[1]}`).join(' ')}
          fill="none"
          stroke={l.id === selectedLane ? '#228be6' : lineFor(l)}
          strokeWidth={l.id === selectedLane ? 4 * unit : Math.max(unit * 1.5, (l.width_m ?? 3) * 0.35)}
          strokeLinecap="round"
          style={{ cursor: onLaneClick ? 'pointer' : undefined }}
          onClick={onLaneClick ? (e) => { e.stopPropagation(); onLaneClick(l, e); } : undefined}
        >
          <title>{`${l.id}${l.speed_limit_kmh ? ` · ${l.speed_limit_kmh} km/h` : ''}${l.road_class ? ` · ${l.road_class}` : ''}`}</title>
        </polyline>
      ))}
      {trails?.map((tr) => tr.points.length > 1 && (
        <polyline key={`t${tr.id}`} points={tr.points.map((p) => p.join(',')).join(' ')} fill="none" stroke={tr.color}
          strokeWidth={Math.max(0.6, unit * 2)} strokeOpacity={0.55} strokeLinecap="round" strokeLinejoin="round" pointerEvents="none" />
      ))}
      {rings?.map((g) => (
        <g key={`r${g.id}`} pointerEvents="none">
          <circle cx={g.x} cy={g.y} r={Math.max(g.r, 8 * unit)} fill="#fa5252" fillOpacity={0.12} stroke="#e03131" strokeWidth={unit * 1.5} strokeDasharray={`${unit * 4} ${unit * 3}`} />
          <text x={g.x + Math.max(g.r, 8 * unit) * 0.75} y={g.y - Math.max(g.r, 8 * unit) * 0.75} fontSize={unit * 13} fontWeight={700} fill="#e03131">{g.label}</text>
          {g.title && <title>{g.title}</title>}
        </g>
      ))}
      {vehicles.map((v) => (
        <g key={`v${v.id}`} transform={`translate(${v.x},${v.y}) rotate(${v.heading_deg ?? 0}) scale(${carScale})`}>
          <rect x={-2.3} y={-1} width={4.6} height={2} rx={0.4} fill={v.color} stroke={dark ? '#000' : '#fff'} strokeWidth={(unit * 0.6) / carScale} />
          {v.label && (
            <text x={3} y={-1.5} fontSize={(unit * 10) / carScale} fill={dark ? '#ced4da' : '#495057'} transform={`rotate(${-(v.heading_deg ?? 0)})`}>
              {v.label}
            </text>
          )}
        </g>
      ))}
      {pins.map((p) => (
        <g key={`p${p.id}`} style={{ cursor: onPinClick ? 'pointer' : undefined }} onClick={() => onPinClick?.(p.id)}>
          {p.shape === 'diamond' ? (
            <rect x={p.x - (p.r ?? 6) * unit} y={p.y - (p.r ?? 6) * unit} width={2 * (p.r ?? 6) * unit} height={2 * (p.r ?? 6) * unit}
              transform={`rotate(45 ${p.x} ${p.y})`} fill={p.color} stroke="#fff" strokeWidth={unit * 1.5} />
          ) : (
            <circle cx={p.x} cy={p.y} r={(p.r ?? 6) * unit} fill={p.color} stroke="#fff" strokeWidth={unit * 1.5} />
          )}
          {p.label && <title>{p.label}</title>}
        </g>
      ))}
      {typeof children === 'function' ? children(unit) : children}
    </svg>
  );
}
