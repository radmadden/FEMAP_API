@echo off
rem ==========================================================================
rem  setup_fem_checks.bat  -  one-time setup for the fem_checks tool
rem
rem  Double-click this file (or run it from a terminal in the repo folder).
rem  It finds Python 3.12, creates a virtual environment in .venv, and then
rem  runs "python -m fem_checks.setup_env", which installs the packages,
rem  sets up VS Code and Jupyter, finds nastran.exe and checks everything.
rem
rem  Why 3.12: pyNastran 1.4.1 needs numpy < 2, and numpy 1.x has no Windows
rem  wheels for Python 3.13 or newer. Install 3.12 from python.org first
rem  (tick "py launcher" and "tcl/tk and IDLE").
rem
rem  Safe to re-run: an existing .venv is reused, existing VS Code settings
rem  are kept (only missing entries are added).
rem ==========================================================================
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY (
    where python >nul 2>nul && python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in [(3,10),(3,11),(3,12)] else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo.
    echo  No Python 3.10 - 3.12 found.
    echo  Install Python 3.12 from https://www.python.org/downloads/windows/
    echo  ^(tick "py launcher" and "tcl/tk and IDLE"^), then run this file again.
    echo.
    pause
    exit /b 2
)
echo Using: %PY%

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    %PY% -m venv .venv || (echo Could not create .venv & pause & exit /b 3)
)

".venv\Scripts\python.exe" -m fem_checks.setup_env %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (echo Setup complete.) else (echo Setup finished with problems - see above.)
pause
exit /b %RC%
