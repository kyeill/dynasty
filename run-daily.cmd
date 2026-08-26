@echo off
REM Daily refresh for every configured sport, invoked by the
REM "dynasty daily refresh" scheduled task. Safe to double-click to test.
REM Output is appended to run.log.

setlocal
set "PY=C:\Users\kyleh\AppData\Local\Programs\Python\Python312-arm64\python.exe"
set "PROJ=%~dp0"
set "PYTHONIOENCODING=utf-8"

cd /d "%PROJ%"
echo. >> "%PROJ%run.log"
echo ===== %DATE% %TIME% ===== >> "%PROJ%run.log"

REM --sport all runs every sport that has a config/<sport>.json; one without
REM a config is skipped rather than treated as an error.
REM --if-stale makes this safe to fire from several triggers a day: whichever
REM lands first does the work, the rest no-op.
REM
REM 12, not 20. The triggers are 07:00, 13:00 and logon, so 12h still blocks a
REM second run the same day (they are only 6h apart) while surviving a run that
REM happened in the EVENING. At 20h an evening run pushed the next refresh to
REM 07:00 the day after next -- a 34h gap, observed 2026-08-24.
"%PY%" rankings.py --sport all --if-stale 12 >> "%PROJ%run.log" 2>&1
set "RC=%ERRORLEVEL%"

REM Rosters too, so a new export or an edited _overrides.csv is picked up
REM without having to remember. Costs nothing when nothing has changed --
REM the naming authority is cached for a week.
"%PY%" rosters.py --sport all --cache >> "%PROJ%run.log" 2>&1

echo exit code: %RC% (rosters: %ERRORLEVEL%) >> "%PROJ%run.log"
endlocal
