# Rules

These rules are hard rules for every contributor, human or AI. If a rule blocks the task, stop and ask. Don't work around it.

---

## 1. Git

- **Agents never commit or push.** Prepare the change. Give the user the exact commands and a commit message only when they ask for them:
  ```powershell
  git add <specific files>
  git commit -m "<imperative summary, <= 72 chars>" -m "<what changed and why>"
  git push origin <branch>
  ```
- Stage **specific files**, not `git add -A`, unless the user asked for everything.
- Branches: `dev` is the main branch. Personal branches are `pranav` and `afif`. Never force-push. When branches diverge, merge; don't overwrite.
- Commit messages are imperative and describe the change ("Add lane-map exporter", "Fix ..."). No `Co-Authored-By` trailers and no AI attribution lines: commits show only the person who makes them.
- Before handing over commands, check that `git status` contains no large or generated files (see section 3).

## 2. Environments

| Code under | Interpreter | Why |
|---|---|---|
| `ml/` | `venv\` (Python 3.12) | torch + ultralytics |
| `simulation/` | `venv_sim\` (Python 3.10) | CARLA wheel is cp310 only |

- New dependency → add it to `requirements.txt` (main) or `simulation/requirements-sim.txt` (sim) in the same change.
- Optional or heavy imports (`airsim`, `pynput`, `stabilo`, `trackeval`) are imported inside the function or a `try` block that exits with a clear "pip install X" message. Keep that pattern.

## 3. Data, weights and large files

Never commit any of the following:
- `CarlaAir-v0.1.7-Windows11-x86_64/` (multi-GB simulator build)
- `ml/data/datasets/`, `ml/data/results/`, `ml/data/raw_footage/`, `simulation/data_export/`
- `*.pt`, `*.pth`, `*.onnx`, `*.mp4`, `runs/`, `PUBLIC POTHOLE DATASET/`
- `venv/`, `venv_*/`, `.env`

Never delete or overwrite results or recordings. New runs go into a **new** timestamped or named folder.

## 4. Numbers and claims

- Every metric written in docs, the tracker or a reply is **measured**: it comes from a run, with its command or output folder named.
- Example values that were not measured are marked *(illustrative)*.
- Don't judge a tracker or detector by raw ID counts alone. "Fewer IDs" can mean wrong merges. Use GT scoring (`eval_tracking.py`, `run_experiment.py`).
- Look at the actual frames or video before concluding why something failed. (The tracker's 2026-09-20 beach-flight log shows a misdiagnosis caused by skipping this.)

## 5. Code

- Match the surrounding code: module docstring with purpose + `Usage:` lines, `argparse` CLI, `pathlib.Path`, small pure functions, comments only where the reason isn't obvious.
- Violation-engine modules import each other as flat modules (`from rules import Engine`). Keep that pattern; don't turn the folder into a package without a reason.
- All rule logic works in **metres and seconds**, never pixels or frame counts. Time comes from `sim_time` (CARLA) or frame PTS (real video), never `frame / fps`.
- Thresholds and tunables go in named constants or config/YAML, with a comment saying where the value came from.
- Tracker changes: new YAML next to the others (`ml/violation_engine/*_ours.yaml` / `*_sim.yaml`). Don't edit the default `tracktrack_ours.yaml` without a measured reason.

## 6. Tests and verification

- Violation engine: `python -m unittest discover -s ml/violation_engine/tests -v` must pass before handing over.
- A new rule or predicate gets a synthetic-track test in `ml/violation_engine/tests/`.
- If something cannot be verified (simulator needed, no GT), say that clearly in the reply. Don't present it as done.

## 7. Research

- Before designing a milestone or a major component, search for the current best approach: recent papers, standards, maintained open-source tools, official docs. The goal is the most advanced and best-built result in every aspect (accuracy, robustness, UI, performance, evaluation).
- Log findings with links in `docs/Research_Notes.md`: what was found, what was adopted, what was rejected and why.
- A paper's numbers are its claims. Adopt an idea only after measuring it on our data (section 4).
- Prefer maintained, openly licensed tools; check that a dependency is still published before relying on it (MinIO images stopped, 2026-10-09).

## 8. Documentation

- Project status lives in `docs/main_project_tracker.md`. A change that moves a stage or produces a result gets a dated row in its **Change Log** (section 6) and, if needed, an updated stage row.
- Design changes to the violation engine go into `docs/Violation_Engine_Architecture.md` (bump the version line).
- Dates are absolute (`2026-10-08`), never "today" or "last week".
