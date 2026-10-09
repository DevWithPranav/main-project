# CLAUDE.md — Instructions for Claude Code

Read these first; they apply to Claude too:
- [AGENTS.md](AGENTS.md): project overview, layout, environments, commands
- [rules.md](rules.md): hard rules
- [architecture.md](architecture.md): system design

## The rules that matter most

1. **Never run `git commit` or `git push`.** Don't offer git commands unprompted either. When the user asks for them, reply with a ready-to-paste PowerShell block:
   ```powershell
   git add <files>
   git commit -m "<short summary>" -m "<details>"
   git push origin pranav
   ```
   The user runs it. Read-only git (`status`, `diff`, `log`) is fine.
2. **Use the right environment.** Use `venv\Scripts\python.exe` for `ml/`. Use `venv_sim\Scripts\python.exe` for `simulation/` (Python 3.10, CARLA). Never install CARLA into the main venv.
3. **Never make up numbers.** Any metric in docs or replies must come from a run you made or one already logged in the tracker. Mark anything else *(illustrative)*.
4. **Never touch large or generated data.** That means `CarlaAir-v0.1.7-*/`, `ml/data/`, `*.pt`, `*.mp4`, `runs/` and `PUBLIC POTHOLE DATASET/`. Don't stage them, delete them or rewrite them.

## Datasets (outside the repo)

All datasets live **outside** this repo, next to it in `C:\Users\prana\Desktop\Main-Project\`. Use them for testing whenever you need data. Read them, but never move, modify or commit them.

| Folder | Contents |
|---|---|
| `..\project data\` | `UAV-benchmark-M\` (UAVDT: M_attr, UAV-benchmark-M, UAV-benchmark-MOTD_v1.0), `VisDrone2019-DET-train (1)\`, `VisDrone2019-VID-train\`, `VisDrone2019-VID-val\`, CarlaAir build (`WindowsNoEditor\`, `CarlaAir zip\`, `AdditionalMaps_Latest.zip`), plus the original zips |
| `..\datasets\` | `VisDrone\` (DET images/labels + zips), `dota8\` |

Data inside the repo (gitignored): `ml/data/` (eval GT, results, recorded-flight outputs) and `simulation/data_export/` (recorded CARLA flights).

Check these folders before saying a dataset is missing. If something the plan needs isn't there (for example highD/inD or UIT-ADrone), tell the user it has to be downloaded or requested.

## Working style for this user

- Platform: Windows 11, PowerShell, RTX 4050 laptop GPU.
- Keep replies short and concrete. Link files as `[name](path)`.
- When a task needs the simulator (CarlaAir running), say so. Don't try to fake that part.
- Verify a change by running it (tests or the script) before you report it as done.

## Note on location

Claude Code auto-loads `CLAUDE.md` only from the repo root (and parent folders), not from `AGENTS/`. To make this file load automatically, put a one-line root `CLAUDE.md` that contains:

```
@AGENTS/CLAUDE.md
```
