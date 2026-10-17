"""Road-surface anomaly events from segmentation masks (Build Plan M9, PRD Section 9.2).

Pure functions, no model and no GPU, so every step has a unit test (ml/pothole/tests/):

    tiles / merge_tile_masks   SAHI-style sliced inference helpers: overlapping tiles, and merging
                               the per-tile masks of one object cut by a tile border
    shadow_features            PRD 9.2 pothole edge case 2: a cast shadow scales the road's
                               brightness but keeps its colour (chromaticity) and its relative
                               texture; a pothole changes material, so colour / texture differ
    waterlogged                PRD 9.2 pothole edge case 1: a pothole overlapping a waterlogging
                               mask gets reduced confidence and the tag "waterlogged"
    ground_polygon / area      mask outline -> ground polygon (map metres) through the camera pose,
                               shoelace area in m^2 (PRD: "segmented area via GSD")
    severity                   PRD 9.2 per class: area term x class weight (+ depth proxy for
                               potholes, alligator-vs-linear for cracks) -> score 0..1 and band
    Deduper                    PRD FAQ "ST_DWithin 3 m + type match": one event per physical
                               defect over all frames of a session; a cluster needs MIN_FRAMES
                               sightings to become an event (single-frame flicker is dropped)
    to_event                   a cluster -> schemas/event.schema.json anomaly event

All thresholds are named constants below with where they came from. Severity constants are a
documented starting point, NOT calibrated: PRD Objective 3 asks for "severity scoring validated
against manual inspection", which needs labelled severities we do not have (tracker, 2026-10-10).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

CATEGORIES = ("pothole", "crack", "waterlogging", "debris")  # schemas/event.schema.json anomaly.type

# --- de-duplication (PRD FAQ: PostGIS ST_DWithin, default 3 m radius, plus type match) -----------
DEDUP_RADIUS_M = 3.0
MIN_FRAMES = 3  # sightings before a cluster becomes an event; one-frame detections are flicker

# --- severity (PRD 9.2; constants are a starting point, not calibrated) --------------------------
# Area at which the size term reaches 1 - 1/e = 0.63. Pothole: PCI/FAA count large potholes in
# 0.5 m^2 units (faapaveair.faa.gov severity levels); waterlogging: a lane-wide (3.5 m) pool ~3 m
# long; debris: ~1 m^2 blocks a wheel path; crack: a 1 m^2 patch of cracking.
AREA_REF_M2 = {"pothole": 0.5, "crack": 1.0, "waterlogging": 10.0, "debris": 1.0}
CLASS_WEIGHT = {"pothole": 1.0, "crack": 0.7, "waterlogging": 0.8, "debris": 0.9}  # PRD: class weighting
POTHOLE_DEPTH_WEIGHT = 0.3  # share of the pothole score from the darkness (depth proxy, PRD 9.2)
CRACK_AREA_ASPECT = 0.3  # min-area-rect short/long side above this = area (alligator) cracking, below = a line
CRACK_LINEAR_FACTOR = 0.6  # PRD 9.2: alligator weighted higher than linear
BANDS = ((0.66, "high"), (0.33, "medium"), (0.0, "low"))

# Plausible ground size of ONE defect; outside it the mask is not that defect. Measured need: the
# close-range PothRGBD model marks whole CARLA intersections (25-3,800 m^2) as potholes from 67 m
# (flight 20261002_001635, 2026-10-10). Upper bounds: a pothole > 20 m^2 is road failure, not a
# pothole; lower bounds are the smallest that matter at our GSD (~0.07 m/px at 67 m, ~4 x 4 px).
SIZE_M2 = {"pothole": (0.05, 20.0), "crack": (0.05, 300.0), "waterlogging": (0.5, 3000.0), "debris": (0.05, 30.0)}

# --- edge cases ----------------------------------------------------------------------------------
WATERLOG_OVERLAP = 0.3  # pothole pixels covered by a waterlogging mask to call it waterlogged
WATERLOG_CONF_FACTOR = 0.7  # "flagged with reduced confidence" (PRD 9.2)
SHADOW_RING_PX = 7  # ring around the mask the inside is compared with
# Shadow test (all must hold): darker than the ring, same chromaticity, same relative texture.
# Chroma / texture tolerances set on synthetic multiplicative shadows (tests) and on the 2025
# model's true potholes on the PothRGBD grouped test split, see Research_Notes 2026-10-10.
SHADOW_MAX_BRIGHTNESS = 0.85
SHADOW_MAX_CHROMA = 0.02
SHADOW_MAX_LOG_TEXTURE = 0.35
SHADOW_CONF_FACTOR = 0.5


# --- tiled (SAHI-style) inference helpers ----------------------------------------------------------

def tiles(w: int, h: int, size: int, overlap: float) -> list[tuple[int, int, int, int]]:
    """Overlapping (x0, y0, x1, y1) windows covering a w x h frame; the last row/column is shifted
    back so every tile is full size (when the frame is at least that big)."""
    step = max(1, int(round(size * (1 - overlap))))

    def starts(n: int) -> list[int]:
        if n <= size:
            return [0]
        s = list(range(0, n - size, step))
        return s + [n - size]
    return [(x, y, min(x + size, w), min(y + size, h)) for y in starts(h) for x in starts(w)]


def mask_iou(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """(IoU, intersection over the smaller mask) of two boolean masks."""
    inter = float(np.logical_and(a, b).sum())
    if inter == 0:
        return 0.0, 0.0
    return inter / float(np.logical_or(a, b).sum()), inter / float(min(a.sum(), b.sum()))


def merge_tile_masks(dets: list[dict], merge_iom: float = 0.5, touch_px: int = 2) -> list[dict]:
    """Merge detections of one frame coming from overlapping tiles (and the full-frame pass).

    dets: {"cls", "conf", "mask" (full-frame bool)}. Two same-class masks are one object when the
    smaller is mostly inside the larger (intersection / smaller >= merge_iom: a duplicate from the
    overlap zone) or when they touch across a tile border (dilated by touch_px they overlap).
    The merged object keeps the union mask and the highest confidence."""
    dets = sorted(dets, key=lambda d: -d["conf"])
    kernel = np.ones((2 * touch_px + 1, 2 * touch_px + 1), np.uint8)
    out: list[dict] = []
    for d in dets:
        for o in out:
            if o["cls"] != d["cls"]:
                continue
            _, iom = mask_iou(o["mask"], d["mask"])
            touch = iom == 0 and np.logical_and(cv2.dilate(o["mask"].astype(np.uint8), kernel) > 0, d["mask"]).any()
            if iom >= merge_iom or (touch and d.get("tile") != o.get("tile")):
                o["mask"] = np.logical_or(o["mask"], d["mask"])
                o["conf"] = max(o["conf"], d["conf"])
                break
        else:
            out.append(dict(d))
    return out


# --- edge cases --------------------------------------------------------------------------------------

def shadow_features(img_bgr: np.ndarray, mask: np.ndarray, ring_px: int = SHADOW_RING_PX) -> dict | None:
    """Inside-vs-ring statistics of one mask. None when the ring is empty (mask fills the frame).

    brightness   mean V inside / mean V of the ring (< 1: darker)
    chroma       distance of the mean normalised (r, g) chromaticity, inside vs ring
    log_texture  |log| of the ratio of relative texture (std / mean of grey levels) inside vs ring
    """
    m = mask.astype(np.uint8)
    k = np.ones((2 * ring_px + 1, 2 * ring_px + 1), np.uint8)
    ring = (cv2.dilate(m, k) > 0) & ~mask.astype(bool)
    inside = mask.astype(bool)
    if ring.sum() < 10 or inside.sum() < 10:
        return None
    f = img_bgr.astype(np.float32) + 1.0
    s = f.sum(axis=2)
    rg = np.stack([f[..., 2] / s, f[..., 1] / s], axis=-1)
    grey = f.mean(axis=2)
    v = f.max(axis=2)

    def rel_tex(sel: np.ndarray) -> float:
        g = grey[sel]
        return float(g.std() / max(g.mean(), 1e-6))
    t_in, t_ring = rel_tex(inside), rel_tex(ring)
    return {"brightness": float(v[inside].mean() / max(v[ring].mean(), 1e-6)),
            "chroma": float(np.linalg.norm(rg[inside].mean(axis=0) - rg[ring].mean(axis=0))),
            "log_texture": float(abs(math.log(max(t_in, 1e-4) / max(t_ring, 1e-4))))}


def looks_like_shadow(feat: dict | None) -> bool:
    return bool(feat) and feat["brightness"] <= SHADOW_MAX_BRIGHTNESS and feat["chroma"] <= SHADOW_MAX_CHROMA \
        and feat["log_texture"] <= SHADOW_MAX_LOG_TEXTURE


def waterlogged(pothole_mask: np.ndarray, water_masks: list[np.ndarray], thr: float = WATERLOG_OVERLAP) -> bool:
    """True when water masks cover >= thr of the pothole's pixels (PRD 9.2 pothole edge case 1)."""
    if not water_masks:
        return False
    n = float(pothole_mask.sum())
    if n == 0:
        return False
    water = np.logical_or.reduce(water_masks)
    return float(np.logical_and(pothole_mask, water).sum()) / n >= thr


# --- geometry ----------------------------------------------------------------------------------------

def mask_outline(mask: np.ndarray, max_pts: int = 64) -> np.ndarray:
    """Outer contour of the largest blob of a mask, as (N, 2) pixel u, v (float), simplified."""
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return np.zeros((0, 2))
    c = max(cs, key=cv2.contourArea)
    if len(c) > max_pts:
        eps = 0.5
        while len(c) > max_pts:
            c = cv2.approxPolyDP(c, eps, True)
            eps *= 1.5
    return c.reshape(-1, 2).astype(float)


def polygon_area(p: np.ndarray) -> float:
    """Shoelace area of a closed polygon (N, 2)."""
    if len(p) < 3:
        return 0.0
    x, y = p[:, 0], p[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def polygon_centroid(p: np.ndarray) -> np.ndarray:
    a = np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1))
    if len(p) < 3 or abs(a) < 1e-12:
        return p.mean(axis=0)
    cr = p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]
    return np.array([np.sum((p[:, 0] + np.roll(p[:, 0], -1)) * cr), np.sum((p[:, 1] + np.roll(p[:, 1], -1)) * cr)]) / (3 * a)


def mask_area_m2(mask: np.ndarray, outline_ground: np.ndarray, outline_px: np.ndarray) -> float:
    """Mask area in m^2: pixel count x (ground area / pixel area of the outline polygon), i.e. the
    local GSD^2 from the projection; holes and thin parts are counted correctly this way."""
    a_px = polygon_area(outline_px)
    if a_px <= 0:
        return 0.0
    return float(mask.sum()) * polygon_area(outline_ground) / a_px


def plausible_size(cls: str, area_m2: float) -> bool:
    lo, hi = SIZE_M2[cls]
    return lo <= area_m2 <= hi


def crack_pattern(mask: np.ndarray) -> str:
    """'alligator' when the crack mask spans an area (its minimum-area rectangle is not thin), else
    'linear' (a long thin crack, straight or curved)."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 3:
        return "linear"
    (_, _), (w, h), _ = cv2.minAreaRect(np.c_[xs, ys].astype(np.float32))
    return "alligator" if max(w, h) > 0 and min(w, h) / max(w, h) >= CRACK_AREA_ASPECT else "linear"


# --- severity --------------------------------------------------------------------------------------

def band(score: float) -> str:
    for lo, name in BANDS:
        if score >= lo:
            return name
    return "low"


def severity(cls: str, area_m2: float, darkness: float | None = None, pattern: str | None = None) -> tuple[float, str]:
    """(score 0..1, band). darkness: 1 - inside/ring brightness (pothole depth proxy, 0..1)."""
    size = 1.0 - math.exp(-max(area_m2, 0.0) / AREA_REF_M2[cls])
    if cls == "pothole" and darkness is not None:
        size = (1 - POTHOLE_DEPTH_WEIGHT) * size + POTHOLE_DEPTH_WEIGHT * float(np.clip(darkness, 0, 1))
    if cls == "crack" and pattern == "linear":
        size *= CRACK_LINEAR_FACTOR
    s = round(float(np.clip(CLASS_WEIGHT[cls] * size, 0.0, 1.0)), 4)
    return s, band(s)


# --- de-duplication over frames --------------------------------------------------------------------

@dataclass
class Sighting:
    """One detection of one frame, already on the ground."""
    frame: int
    t_s: float
    cls: str
    conf: float
    x: float
    y: float
    area_m2: float
    score: float
    tags: tuple[str, ...] = ()
    polygon: np.ndarray | None = None  # ground outline (N, 2) map metres
    extra: dict = field(default_factory=dict)


@dataclass
class Cluster:
    cls: str
    sightings: list[Sighting] = field(default_factory=list)

    @property
    def xy(self) -> np.ndarray:
        w = np.array([s.conf for s in self.sightings])
        p = np.array([[s.x, s.y] for s in self.sightings])
        return (p * w[:, None]).sum(axis=0) / w.sum()

    def best(self) -> Sighting:
        """The sighting shown as evidence: highest confidence, then largest."""
        return max(self.sightings, key=lambda s: (s.conf, s.area_m2))

    def reach(self) -> float:
        """Join radius: the PRD 3 m, or more for a defect bigger than that (a long pool or crack field
        seen from several frames has a centroid that moves with the visible part)."""
        a = max((s.area_m2 for s in self.sightings), default=0.0)
        return max(DEDUP_RADIUS_M, math.sqrt(a / math.pi))


class Deduper:
    """Online greedy clustering by type and ground distance (PRD: ST_DWithin 3 m + type match)."""

    def __init__(self, radius_m: float = DEDUP_RADIUS_M, min_frames: int = MIN_FRAMES):
        self.radius_m, self.min_frames = radius_m, min_frames
        self.clusters: list[Cluster] = []

    def add(self, s: Sighting) -> Cluster:
        best, bd = None, math.inf
        for c in self.clusters:
            if c.cls != s.cls:
                continue
            d = float(np.hypot(*(c.xy - (s.x, s.y))))
            if d <= max(self.radius_m, c.reach()) and d < bd:
                best, bd = c, d
        if best is None:
            best = Cluster(s.cls)
            self.clusters.append(best)
        best.sightings.append(s)
        return best

    def events(self) -> list[Cluster]:
        """Clusters seen on at least min_frames distinct frames, ordered by first sighting."""
        keep = [c for c in self.clusters if len({s.frame for s in c.sightings}) >= self.min_frames]
        return sorted(keep, key=lambda c: min(s.t_s for s in c.sightings))


def to_event(c: Cluster, event_id: str, session: str | None = None, lane_id: str | None = None,
             snapshot: str | None = None) -> dict:
    """A cluster -> anomaly event (schemas/event.schema.json). Score / area are the median over the
    sightings (robust to one bad mask), confidence the mean x the share of tagged-ok sightings."""
    b = c.best()
    xy = c.xy
    area = float(np.median([s.area_m2 for s in c.sightings]))
    score = float(np.median([s.score for s in c.sightings]))
    conf = float(np.mean([s.conf for s in c.sightings]))
    tag_counts: dict[str, int] = {}
    for s in c.sightings:
        for t in s.tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    n = len(c.sightings)
    tags = sorted(t for t, k in tag_counts.items() if k * 2 >= n)  # a tag on at least half the sightings
    frames = sorted({s.frame for s in c.sightings})
    ev = {"kind": "anomaly", "event_id": event_id, "type": c.cls, "t_s": round(b.t_s, 3), "frame": int(b.frame),
          "x": round(float(xy[0]), 2), "y": round(float(xy[1]), 2), "lane_id": lane_id, "zone_id": None,
          "severity_score": round(score, 4), "severity_band": band(score), "area_sq_m": round(area, 3),
          "recurrence_count": 1, "tags": tags, "confidence": round(min(max(conf, 0.0), 1.0), 4), "status": "flagged",
          "evidence": {"frame": int(b.frame), "telemetry": {
              "first_s": round(min(s.t_s for s in c.sightings), 3), "last_s": round(max(s.t_s for s in c.sightings), 3),
              "n_sightings": n, "n_frames": len(frames),
              "spread_m": round(float(np.max([np.hypot(s.x - xy[0], s.y - xy[1]) for s in c.sightings])), 2),
              "polygon": None if b.polygon is None else [[round(float(u), 2), round(float(v), 2)] for u, v in b.polygon],
              **b.extra}}}
    if snapshot:
        ev["evidence"]["snapshot"] = snapshot
    if session:
        ev["session_id"] = session
    return ev
