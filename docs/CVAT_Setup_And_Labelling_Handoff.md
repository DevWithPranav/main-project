# CVAT Setup & Labelling Handoff

Status: v1 (2026-09-26). For: whoever picks up ground-truth labelling next. Quick runbook — full background and rules are in `docs/Ground_Truth_Creation_Guide.md`; read that too before labelling.

---

## 1. Install (already done once — here for a fresh machine)

Requires **WSL2 + Docker Desktop** first (see `docs/Ground_Truth_Creation_Guide.md` if these aren't installed, or just: `wsl --install`, restart, install Docker Desktop, launch it once).

```powershell
git clone https://github.com/cvat-ai/cvat
cd cvat
docker compose up -d
docker compose exec cvat_server python3 manage.py createsuperuser
```

The last command asks for a username, email, password — that becomes your login. Then open **http://localhost:8080** and log in with that username (not the email) and password.

To check it's running later: `docker compose ps` (all services should say `running`). To stop it: `docker compose down` (from the `cvat` folder). To start it again next time: `docker compose up -d` from that same folder — no need to re-create the account.

---

## 2. What to do with the frames / video once CVAT is up

### 2.1 Which source file to upload

| Source | Upload | Why |
|---|---|---|
| Real drone footage, prepared with ffmpeg (`clip_1080p.mp4`) | **The video file itself** | It's constant frame rate, so CVAT's frame numbers match what our pipeline reads |
| CARLA recording (`record_flight.py` output) | **The `frames/` folder, zipped** — not `flight.mp4` | CARLA's capture rate isn't constant; `flight.mp4` is a lossy nominal-fps re-encode for quick review only. Labelling it would misalign frame numbers against the real data |

Zip CARLA frames like this:
```powershell
cd simulation\data_export\recorded_flights\<run_id>
Compress-Archive -Path frames\*.jpg -DestinationPath frames.zip
```

Since this instance is self-hosted, there's **no size limit** — upload the original file, don't compress it for CVAT.

### 2.2 Create the project (once)

**Projects → Create new project**
- Name: e.g. `Vehicle Tracking GT`
- Labels: add these three, plus one more:
  - `car` (covers car, van, minivan, SUV, pickup)
  - `bus` (covers bus, minibus)
  - `truck`
  - `ignore` (for areas too dense/tiny to label reliably — not a vehicle class)
- Optional: on `car`/`bus`/`truck`, add an attribute `parked` (checkbox type) — lets us report parked-vehicle results separately later.

### 2.3 Create the task

**Tasks → Create new task**, attach it to the project above, then:
- **Select files**: the video file, or the frames `.zip` (CVAT auto-detects a zip of images as an image sequence and orders them by filename — the frames are already zero-padded so the order is correct).
- **Advanced settings**:
  - **Image quality: 95–100** (default 70 blurs small vehicles in the editor — doesn't change the labels, but makes them harder to place accurately).
  - Frame step: 1 (label every frame).
  - Start/stop frame: leave empty (whole clip).
  - Segment size: whole clip in one job if one person is labelling; otherwise split with **overlap 30** frames (~1 s) so IDs can be reconciled at the boundary.
- Click **Submit**.

### 2.4 Pre-annotate before labelling by hand (saves most of the work)

Our tracker's output can be imported as a starting point that you then correct, instead of drawing every box from scratch. This needs `export_cvat.py` (converts `trajectories_stitched.csv` to a CVAT-importable MOT 1.1 zip) — **not written yet**. Until it exists, send the video/frames to Afif to get the pre-annotation zip generated, then:

**Task → Actions (⋮) → Upload annotations → format: MOT 1.1** → choose the zip.

If pre-annotations aren't ready yet, you can start labelling from scratch (Section 3 below still applies) and import them later — CVAT lets you upload annotations onto an already-created task.

**Warning:** if you accept what's already there without checking, the ground truth inherits our tracker's mistakes and the results look better than they really are. Check hardest for: vehicles with no box at all (missed), one vehicle whose ID changes partway (a switch), and boxes on snow/roofs/shadows (false positives).

---

## 3. Labelling workflow (two passes)

Open the task → click **Job #1** (or your job, if split).

**Pass 1 — boxes.** Use **Track** mode (not Shape) so IDs persist across frames:
- Draw a box on a keyframe, skip ~10–15 frames, adjust the box → CVAT interpolates in between.
- Add extra keyframes wherever the vehicle turns, speeds up, or the camera moves suddenly (interpolation is linear).
- Vehicle leaves view or fully hidden → mark the track **"outside"** on that frame (don't delete the track); switch "outside" off when it reappears → same ID continues.
- Delete false-positive pre-annotation boxes (snow, roofs, shadows, road markings).

**Pass 2 — identities.** Watch each ID from its first frame to its last:
- Two tracks on one vehicle (an ID switch) → **merge**.
- One track jumping between two vehicles (a swap) → **split**, then merge each piece into the right vehicle.
- Pay special attention to tracks that **start or end away from the frame edge** — that's the usual sign of a missed merge.

**Labelling rules** (full table in `docs/Ground_Truth_Creation_Guide.md`, Section 6) — the essentials:
- Box tightly, **exclude the shadow**.
- Van/SUV/minivan/pickup → `car`. Minibus → `bus`.
- Motorcycles, bicycles, pedestrians: don't label.
- Vehicle at the frame edge: label only if ≥ 50% visible.
- Snow-covered parked car → still `car`. Snow pile / roof that looks like a vehicle → don't label (we want to count these as false positives).
- Only give a re-entering vehicle its old ID back if you're certain it's the same one — otherwise a new ID.

---

## 4. Quality check before exporting

- Play at 0.25× speed with IDs shown; follow each ID start to end.
- Spot-check 10 random frames: count visible vehicles vs. drawn boxes.
- Re-check every track that starts/ends mid-frame.
- Confirm every camera move: do IDs survive it correctly?

---

## 5. Export and deliver

Export **twice** from the task menu (**Actions → Export task dataset**):
1. **Format: MOT 1.1** — this is what gets scored.
2. **Format: CVAT for video 1.1** — keeps keyframes/attributes, so it can be re-edited later.

Deliver into `ml/data/eval/gt_1080p/` (shared drive — video/labels are too large for git):
```
clip_1080p.mp4          the exact labelled video (or the CARLA frames folder), untouched
clip_1080p.sha256       certutil -hashfile clip_1080p.mp4 SHA256
gt_mot11.zip            MOT 1.1 export
gt_cvat_video.zip       CVAT for video export
gt_info.json            see template in docs/Ground_Truth_Creation_Guide.md, Section 9
```

Ping Afif (W4) once delivered — `import_gt.py` turns this into our scoring CSV and runs the automated QA checks.

---

## 6. Quick reference — commands used to set this up

```powershell
wsl --install
# restart Windows, then install Docker Desktop, launch it once

git clone https://github.com/cvat-ai/cvat
cd cvat
docker compose up -d
docker compose exec cvat_server python3 manage.py createsuperuser
```

Start/stop the already-installed server later:
```powershell
cd cvat
docker compose up -d      # start
docker compose down       # stop
docker compose ps         # check status
docker compose logs cvat_server --tail 50   # debug if something's wrong
```

---

## 7. See also

- `docs/Ground_Truth_Creation_Guide.md` — full guide: recording footage, ffmpeg commands, CARLA traffic setup, all labelling rules, tools comparison, advanced techniques, CARLA automatic ground truth.
- `docs/Vehicle_Detection_Tracking_Improvement_Plan.md` — where this ground truth fits in the overall plan (Phase 2).
