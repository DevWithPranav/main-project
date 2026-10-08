# CLAUDE.md — Instructions for Claude Code

Read these first; they apply to Claude too:
- [AGENTS.md](AGENTS.md): project overview, layout, environments, commands
- [rules.md](rules.md): hard rules
- [architecture.md](architecture.md): system design

## The rules that matter most

1. **Never run `git commit` or `git push`.** When the work is done, end the reply with a ready-to-paste PowerShell block:
   ```powershell
   git add <files>
   git commit -m "<short summary>" -m "<details>"
   git push origin pranav
   ```
   The user runs it. Read-only git (`status`, `diff`, `log`) is fine.
2. **Use the right environment.** Use `venv\Scripts\python.exe` for `ml/`. Use `venv_sim\Scripts\python.exe` for `simulation/` (Python 3.10, CARLA). Never install CARLA into the main venv.
3. **Never make up numbers.** Any metric in docs or replies must come from a run you made or one already logged in the tracker. Mark anything else *(illustrative)*.
4. **Never touch large or generated data.** That means `CarlaAir-v0.1.7-*/`, `ml/data/`, `*.pt`, `*.mp4`, `runs/` and `PUBLIC POTHOLE DATASET/`. Don't stage them, delete them or rewrite them.

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
