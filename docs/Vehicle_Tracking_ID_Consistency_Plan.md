# Vehicle Tracking — ID Consistency Plan

Status: **planning** (no implementation started yet). Owner: Afif.
Feeds into Objective 1 (`docs/main_project_tracker.md`, Section 2, Stage 3 — Tracking layer)
and Objective 8 (Section 2.3 — CARLA/AirSim simulation validation).

## 1. Problem Statement

Vehicle detection is working (Objective 1, Stage 1 — done, see tracker Section 2.2).
The current gap is **tracking**: the same physical vehicle sometimes gets assigned a
new track ID mid-video instead of keeping one consistent ID for its whole time in
frame. This breaks anything built on top of trajectories — speed estimation,
wrong-way detection, no-parking dwell time all assume one ID = one vehicle for its
full appearance.

Current setup: `model.track()` (Ultralytics) with a custom `bytetrack_sim.yaml`
(`ml/violation_engine/bytetrack_sim.yaml`) — plain ByteTrack, loosened thresholds,
no appearance model, no camera-motion compensation.

## 2. Research Summary

### 2.1 Production tracker families (mature, drop-in via Ultralytics)

| Tracker | Appearance (ReID) | Camera-motion compensation | Speed | Best for |
|---|---|---|---|---|
| **ByteTrack** (current) | No — IoU/motion only | No | Fastest | Static or near-static camera, well-separated objects |
| **BoT-SORT** | Optional (`with_reid: true`) | Yes (`gmc_method`: sparseOptFlow/ECC/ORB/SIFT) | ~30% slower than ByteTrack with ReID on | Moving camera, crossing/occluding objects — matches our setup |
| **OC-SORT / Deep OC-SORT** | Deep OC-SORT: yes | No native GMC | Similar to BoT-SORT | Erratic/non-linear motion (sports, animals) — less relevant, our vehicles move smoothly |

**Why this matters for us specifically:** ByteTrack only asks "is there a box
roughly where I expect this object next, based on box overlap/motion." It has no
memory of what a vehicle looks like, and no way to separate "the vehicle moved" from
"the camera moved." Our drone is airborne and moving throughout every flight — every
background pixel is in motion — which is exactly the scenario BoT-SORT's GMC step
and ReID embedding were built to handle, and exactly what plain ByteTrack was never
designed for.

BoT-SORT-ReID currently outperforms ByteTrack on MOTA/IDF1/HOTA on the standard
MOT17/MOT20 pedestrian-tracking benchmarks specifically because of the appearance
model closing exactly this kind of ID-switch gap. It's also already the Ultralytics
default tracker when none is specified, and needs zero new dependencies — it ships
in the same `ultralytics` package we already use.

### 2.2 UAV/aerial-specific state of the art (research-grade, not drop-in)

These target our exact problem (moving aerial camera + ground vehicles) more
directly than generic trackers, at the cost of being research code rather than a
maintained package:

- **DroneMOT** (2024) — explicitly models *simultaneous* drone motion and object
  motion, rather than assuming a static or simply-panning camera. Closest published
  match to our setup (drone flying while tracking ground vehicles). Code:
  [github.com/PenK1nG/DroneMOT](https://github.com/PenK1nG/DroneMOT).
- **AMOT** (AAAI 2026, "Tracking the Unstable") — appearance-motion consistency
  matrix + motion-aware track continuation; reported training-free/plug-and-play,
  state-of-the-art on VisDrone2019, UAVDT, and VT-MOT-UAV. Code:
  [github.com/ydhcg-BoBo/AMOT](https://github.com/ydhcg-BoBo/AMOT). Built on the
  FairMOT/STCMOT lineage.
- **STCMOT** (2024) — spatio-temporal cohesion learning, prior state-of-the-art on
  UAVDT before AMOT.

None of these are pip-installable; each needs its own repo cloned, its detector
output format adapted to accept our YOLO26l boxes, and — per their own papers —
evaluation against our specific footage before trusting reported numbers, since all
of their benchmark numbers are on VisDrone-MOT/UAVDT, not CARLA synthetic footage or
our own real-world captures.

### 2.3 Domain-gap risk carried over from the detector work

Ultralytics' bundled ReID embedding model is generic (trained on pedestrian/vehicle
ReID datasets like Market1501/VeRi), not on VisDrone-style or CARLA-rendered aerial
crops. This is the same class of domain-gap risk already documented for the
detector itself (`docs/main_project_tracker.md` Section 2.2/2.3) — the appearance
model may be less discriminative on our specific footage than its benchmark numbers
suggest, and should be validated on our own data before being trusted.

## 3. Recommendation

**Adopt a phased approach: cheap/proven first, research-grade only if needed.**

Jumping straight to DroneMOT/AMOT before establishing a real evaluation baseline
would repeat the same mistake already made once this project (see tracker Section
2.3 — ByteTrack threshold tuning was tried, and judged a failure, on footage that
turned out to have no vehicles in it at all; conclusions require solid ground truth
first). BoT-SORT-ReID is a same-package, low-risk swap that directly targets both
identified causes of our ID switches (camera motion, appearance-blind matching) and
should be measured properly before deciding whether the aerial-specific research
trackers are actually needed.

## 4. Phased Implementation Plan

### Phase 1 — Add a real tracking-quality metric (prerequisite, not optional)

Today, tracking quality is judged by eyeballing annotated video + counting rows/
unique IDs in `trajectories.csv` (see the CARLA validation history in
`docs/main_project_tracker.md` Section 2.3). That's enough to catch total failure,
not enough to compare trackers or tune thresholds with confidence.

- Add ID-switch counting and, ideally, MOTA/IDF1/HOTA computation using the
  `motmetrics` Python package (`pip install motmetrics`) against a small
  hand-labeled ground-truth set.
- Ground truth source: a short segment (30–60s) of one manually-recorded flight
  (`simulation/carla_scripts/record_flight.py` output) with vehicle boxes+IDs
  hand-annotated frame-by-frame (CVAT or similar) — expensive to do for a whole
  flight, cheap enough for one short validation clip.
- Deliverable: a small script, e.g. `ml/violation_engine/eval_tracking.py`, that
  takes a predicted `trajectories.csv` + a ground-truth file and prints ID-switch
  count, MOTA, IDF1.

Without this, every later phase below is judged the same unreliable way tracking
has been judged so far.

### Phase 2 — Switch to BoT-SORT-ReID + GMC (cheap, no new dependencies)

- Add `ml/violation_engine/botsort_sim.yaml` (mirrors the existing
  `bytetrack_sim.yaml` pattern) with:
  - `with_reid: true` (off by default in Ultralytics — must be explicitly enabled)
  - `gmc_method: sparseOptFlow` (cancels drone ego-motion before matching; `ecc` is
    more accurate but slower — try `sparseOptFlow` first given the 2-min/frame
    budget already tight on this hardware)
  - Same relaxed `track_high_thresh`/`track_low_thresh`/`track_buffer` starting
    point as `bytetrack_sim.yaml`, re-tuned using Phase 1's metric rather than by
    eyeballing
- Wire `process_recorded_flight.py` and `run_sim_validation.py`'s `SIM_TRACKER`
  constant to accept a `--tracker` CLI flag, so both `bytetrack_sim.yaml` and
  `botsort_sim.yaml` can be A/B tested on the exact same recorded footage.
- Re-run against the Phase 1 ground-truth clip; compare ID-switch count and
  MOTA/IDF1 directly against the current ByteTrack baseline.
- **Expected outcome, per the research above:** meaningful reduction in ID
  switches at real vehicle crossings/occlusions, ~30% slower per-frame inference
  (acceptable for this project's offline validation use case — nothing here runs
  live except the already-slower `live_fly_and_track.py`).

### Phase 3 — Only if Phase 2 isn't enough: aerial-specific tracker

Triggered only if Phase 2's measured ID-switch rate is still unacceptable on real
recorded footage (not synthetic beach-flight artifacts):

- Evaluate DroneMOT first (closer match to our exact drone-motion + object-motion
  scenario than AMOT's more general aerial focus).
- Integration shape: adapt our YOLO26l detection output into whichever input format
  DroneMOT's reference tracker expects (typically a per-frame detection list with
  boxes+scores+classes — close to what `extract_trajectories.py` already produces),
  run its association logic in place of Ultralytics' `model.track()`.
- Budget this as a multi-day research-code integration, not a config swap —
  unmaintained academic repos commonly need dependency/version fixes before they
  even run.

### Phase 4 — Domain-adapt the ReID embedding (only if Phase 2's appearance model underperforms specifically due to domain gap)

- If Phase 1's metric shows BoT-SORT's appearance matching is *unreliable*
  specifically (not just occasionally wrong) on VisDrone/CARLA-style footage, that
  points at the generic ReID embedding rather than the association logic itself.
- Fix: fine-tune or replace the ReID feature extractor using vehicle crops pulled
  from our own tracked footage (a lightweight triplet-loss fine-tune, not a
  from-scratch ReID model) — mirrors the same "domain gap → fine-tune on our own
  data" pattern already applied to the detector itself.

## 5. What NOT to do (informed by this project's own history)

- Don't tune tracker thresholds against footage that hasn't been visually verified
  to contain continuous real traffic first (see tracker Section 2.3 — this exact
  mistake already cost a debugging cycle on the CARLA beach-flight data).
- Don't adopt a research-grade tracker (Phase 3) before Phase 1's metric exists —
  there'd be no reliable way to confirm it's actually better here, only that it's
  better on VisDrone-MOT/UAVDT's specific footage.
- Don't conflate "more unique track IDs than expected" with "worse tracking" or
  vice versa without checking *why* — could be genuine ID switches, could be the
  detector losing/reacquiring the vehicle (a detection problem, not a tracking
  one — see the "confidence dip" case already discussed for this project).

## 6. Immediate Next Action

`ml/violation_engine/eval_tracking.py` (Phase 1's scoring script) is written, but
the ground-truth clip it needs has not been hand-labeled — **decision (2026-09-26):
proceed to Phase 2 (BoT-SORT-ReID) without it**, to get a config in place and
compare against ByteTrack by eye first. This explicitly reopens the risk flagged in
Section 5: without Phase 1's metric, "BoT-SORT looks better" on any given clip is
an eyeballed judgment, not a confirmed number, and could be re-litigated once/if
the ground-truth clip is produced later.

Phase 2 status: `ml/violation_engine/botsort_sim.yaml` added; `process_recorded_flight.py`
and `run_sim_validation.py` both take a `--tracker {bytetrack,botsort}` flag so the
two configs can be run back-to-back on the same footage. Not yet run against real
recorded-flight footage — next step is to actually run both on the same clip and
compare the annotated videos / track-ID counts.
