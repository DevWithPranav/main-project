@echo off
rem Start CarlaAir, then open the drone controls (keyboard + gamepad) in their own window.
rem Run from an Anaconda Prompt with the carlaAir env active:
rem     conda activate carlaAir
rem     cd /d D:\Main-Project\main-project\simulation\carla_scripts
rem     start_carla_and_fly.bat
rem Extra arguments go to StartCarlaAir.bat (defaults below: Town10HD, Epic, 80 vehicles, 20 walkers), e.g.
rem     start_carla_and_fly.bat Town03 --traffic-vehicles 120
rem Then record from a second prompt:  python record_flight.py --vehicles 0 --labels --no-control

setlocal
set "HERE=%~dp0"
set "CARLAAIR=%HERE%..\..\CarlaAir-v0.1.7-Windows11-x86_64"

if "%~1"=="" (
    call "%CARLAAIR%\StartCarlaAir.bat" Town10HD --res 1280x720 --quality Epic --traffic-vehicles 80 --traffic-walkers 20
) else (
    call "%CARLAAIR%\StartCarlaAir.bat" %*
)
if errorlevel 1 (
    echo CarlaAir did not start - not opening the controls.
    exit /b 1
)

start "Drone controls" /D "%HERE%" python fly_drone.py
echo Drone controls opened in a new window. Record from another prompt:
echo     python record_flight.py --vehicles 0 --labels --no-control
