@echo off
rem Double-click: the whole Town04 live run (see start_town04_live.ps1). Extra arguments are passed on, e.g.
rem     start_town04_live.bat -Session demo_1 -Stoppers 3
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_town04_live.ps1" %*
pause
