@echo off
rem ==========================================================================
rem  run_fem_checks.bat  -  start the FEM validation checks
rem
rem  Double-click: opens the GUI.
rem  With a settings file: run_fem_checks.bat "<output folder>\fem_checks.json"
rem      repeats that run without the GUI; add --no-run to re-evaluate the
rem      outputs already in the folder without running Nastran.
rem
rem  Run setup_fem_checks.bat once first.
rem ==========================================================================
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo The Python environment is missing. Run setup_fem_checks.bat first.
    pause
    exit /b 2
)
rem Run from the folder above, so "fem_checks" is importable as a package.
cd /d "%~dp0.."
if "%~1"=="" (
    rem GUI: pythonw runs it without leaving a console window open.
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m fem_checks
    exit /b 0
)
"%~dp0.venv\Scripts\python.exe" -m fem_checks %*
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
