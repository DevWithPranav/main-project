@echo off
rem Double-click: stop everything start_town04_live.bat started - drone controls, stream, highway traffic,
rem live pipeline (finishes cleanly, writes summary.json), watchdog, backend, dashboard, docker and CarlaAir.
rem For a lighter stop (keep CarlaAir and the backend up) run stop_town04_live.ps1 without -All -Docker -Carla.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_town04_live.ps1" -All -Docker -Carla %*
pause
