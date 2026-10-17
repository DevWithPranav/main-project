# AGENTS.md — Guide for AI coding agents

This guide is for any AI coding agent (Claude Code, Codex, Copilot, Cursor and others) working in this repo.
Read these three files before changing anything:

1. **[rules.md](rules.md)**: hard rules (git, environments, data, numbers). Follow them strictly.
2. **[architecture.md](architecture.md)**: how the system fits together.
3. `docs/main_project_tracker.md`: what is done, what is in progress, and the change log.

---

## Project in one paragraph

This is the **Autonomous Aerial Surveillance Framework**. A drone films traffic from above. A YOLO detector and the TrackTrack tracker turn each frame into vehicle boxes with stable IDs. The **violation engine** then converts those tracks into traffic-violation events: no-parking, wrong-way, illegal U-turn, speeding, lane violation, zebra crossing and highway stopping. A CARLA + AirSim simulator (CarlaAir) supplies synthetic flights with exact ground truth. A separate pothole/road-surface track is also in progress. The product spec is `docs/Product_Requirements_Document.md`.

Team: **Pranav**, **Afif** (detection/tracking, simulation), **Hussain** (violation rule logic).
Branches: `dev` is the main branch. Each person also has their own branch: `pranav`, `afif`.

---

## Repo layout

```
main-project/
├── AGENTS/                  agent guides (this folder)
├── docs/                    PRD, phase plans, tracker, architecture docs, paper
├── schemas/                 shared JSON schemas: events, config profiles, the 34 conditions
├── backend/                 FastAPI service (Build Plan M5, not built yet)
├── frontend/                Configuration Dashboard + 3D twin (M6, M7, not built yet)
├── services/                live pipeline (M4, not built yet)
├── infra/                   docker init files (PostGIS); root docker-compose.yml runs the services
├── ml/
│   ├── detection/           detector training / retraining / comparison
│   ├── tracking/            tracking demo
│   ├── violation_engine/    tracking pipeline + violation engine (main active code)
│   │   ├── configs/         scene lane maps (CARLA), real-site configs, profiles/
│   │   ├── tests/           unittest suite
│   │   └── *.yaml           tracker configs (tracktrack_ours.yaml = default)
│   ├── pothole/             road-surface anomaly track
│   ├── geo_projection/      pixel -> ground/GPS (early)
│   ├── models/              base YOLO weights (*.pt, gitignored)
│   └── data/                datasets, eval GT, results (mostly gitignored)
├── simulation/
│   ├── carla_scripts/       fly / record / traffic / lane-map export (CARLA env)
│   └── violation_scenarios/ staged violation scenarios (CARLA env)
├── CarlaAir-v0.1.7-.../     simulator build, gitignored, never commit
├── requirements.txt         main ML env
└── simulation/requirements-sim.txt   CARLA/AirSim env
```

---

## Datasets

All datasets live outside the repo, in `C:\Users\prana\Desktop\Main-Project\project data\` (UAVDT, VisDrone DET/VID/MOT, CarlaAir build) and `C:\Users\prana\Desktop\Main-Project\datasets\` (VisDrone, dota8). Use them for testing. They are read-only. See `CLAUDE.md` for the full list.

---

## Environments (two, they cannot be merged)

| Env | Python | Folder | Used for |
|---|---|---|---|
| Main | 3.12 | `venv\` | everything under `ml/` (torch, ultralytics, violation engine) |
| Sim | 3.10 | `venv_sim\` | `simulation/` scripts (`carla`, `airsim`). The CARLA wheel is cp310 only |

```powershell
# main
venv\Scripts\activate
# sim
venv_sim\Scripts\activate
```

Setup from scratch: see the header comments in `requirements.txt` and `simulation/requirements-sim.txt`.

---

## Common commands

```powershell
# Unit tests (violation engine), main env
python -m unittest discover -s ml/violation_engine/tests -v

# Detect + track a recorded CARLA flight, main env
python ml/violation_engine/process_recorded_flight.py <flight dir> --tracker tracktrack_ours

# Violation engine on a flight, main env
python ml/violation_engine/run_violations.py <flight dir> --scene ml/violation_engine/configs/scenes/Town05.json
python ml/violation_engine/run_violations.py <flight dir> --scene ... --oracle   # rules on perfect perception

# Score violations
python ml/violation_engine/eval_violations.py <pipeline dir> <oracle dir> --scenario <scenario_log.json>

# Simulator side, sim env, CarlaAir running
python simulation/carla_scripts/record_flight.py --labels
python simulation/violation_scenarios/stage_violations.py --out <flight>/scenario_log.json
```

Every script has a module docstring with its exact usage. Read it before running the script.

---

## Workflow for an agent

1. **Read before writing.** Open the script's docstring, the relevant section of `docs/Violation_Engine_Architecture.md` or the plan doc, and the tracker.
2. **Make the change** and match the surrounding style (see rules.md, "Code").
3. **Verify** it: run the unit tests, and run the script on real data if you can. Report the actual output.
4. **Log it.** Add a row to the Change Log in `docs/main_project_tracker.md` if the change matters to the project status.
5. **Leave git to the user.** Do not commit or push. Give commands and a commit message only when asked (rules.md, "Git").
