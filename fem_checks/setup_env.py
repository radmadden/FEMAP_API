"""One-stop environment setup for fem_checks on a Windows workstation.

Run it through ``setup_fem_checks.bat`` in the repo root (double-click). The .bat finds
Python 3.12, creates ``.venv`` in the repo and then runs this module inside it:

    .venv\\Scripts\\python -m fem_checks.setup_env [--no-dev] [--no-vscode] [--no-tests]

What it does, in order:
  1. checks the Python version. pyNastran 1.4.1 needs numpy < 2, and numpy 1.26 (the last
     1.x) publishes Windows wheels only up to Python 3.12, so 3.13+ cannot install it
     (verified against PyPI 2026-11-04). Supported: 3.10 - 3.12;
  2. pip-installs fem_checks/requirements.txt (+ requirements-dev.txt unless --no-dev:
     pytest, Jupyter, ipykernel);
  3. registers a Jupyter kernel "fem_checks (.venv)" so the design notebook runs in VS Code;
  4. writes/merges .vscode/settings.json, extensions.json and launch.json (existing keys
     are kept; only missing entries are added) and installs the Python + Jupyter
     extensions if the ``code`` command is on PATH;
  5. looks for nastran.exe in the usual Siemens install folders and pre-fills the GUI's
     remembered settings with the one it finds;
  6. verifies every import (numpy, scipy, matplotlib, pyNastran, reportlab, tkinter) and
     parses a sample .f06, then runs the test suite unless --no-tests.

Nothing here touches FEMAP, Nastran or the model files.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
REPO = PKG.parent
SUPPORTED = ((3, 10), (3, 12))  # inclusive range

EXTENSIONS = ["ms-python.python", "ms-toolsai.jupyter"]

# Usual Simcenter Nastran locations (FEMAP bundle first; the user's runs came from
# C:/PROGRAM FILES/SIEMENS/FEMAP 2512/NASTRAN per the sample .f06s).
NASTRAN_GLOBS = [
    r"C:\Program Files\Siemens\FEMAP*\NASTRAN\bin\nastran*.exe",
    r"C:\Program Files\Siemens\Simcenter3D*\NXNASTRAN\bin\nastran*.exe",
    r"C:\Program Files\Siemens\Simcenter*Nastran*\bin\nastran*.exe",
    r"C:\Program Files\Siemens\NX*\NXNASTRAN\bin\nastran*.exe",
]


def say(msg: str = "") -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------ 1 python
def python_ok(version=sys.version_info) -> tuple[bool, str]:
    v = (version[0], version[1])
    if SUPPORTED[0] <= v <= SUPPORTED[1]:
        return True, f"Python {v[0]}.{v[1]} OK"
    return False, (f"Python {v[0]}.{v[1]} is not supported. pyNastran 1.4.1 needs numpy < 2, "
                   "which has Windows wheels only up to Python 3.12. Install Python 3.12 from "
                   "python.org (tick 'tcl/tk and IDLE' so the GUI works) and re-run "
                   "setup_fem_checks.bat.")


# ------------------------------------------------------------------ 2 pip
def pip_install(files: list[Path]) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "pip"]
    subprocess.run(cmd, check=False)
    args = [sys.executable, "-m", "pip", "install"]
    for f in files:
        args += ["-r", str(f)]
    say("  " + " ".join(args))
    r = subprocess.run(args)
    if r.returncode != 0:
        say("\n  pip failed. On a company network this is usually the proxy or a blocked "
            "pypi.org. Try:\n    .venv\\Scripts\\python -m pip install --proxy "
            "http://<proxy:port> -r fem_checks\\requirements.txt\nor ask IT to allow "
            "pypi.org and files.pythonhosted.org.")
        return False
    return True


# ------------------------------------------------------------------ 3 kernel
def register_kernel() -> bool:
    r = subprocess.run([sys.executable, "-m", "ipykernel", "install", "--user",
                        "--name", "fem_checks", "--display-name", "fem_checks (.venv)"])
    return r.returncode == 0


# ------------------------------------------------------------------ 4 VS Code
def _merge(path: Path, wanted: dict) -> dict:
    """Load JSON at path (if any), add keys from ``wanted`` that are missing, write back.
    Existing user values always win. Lists named 'recommendations'/'configurations' are
    merged by value/name rather than replaced."""
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            # VS Code allows comments in these files; don't risk mangling one we can't parse.
            say(f"  {path} has comments or isn't plain JSON; left unchanged. Add these by hand:")
            say("  " + json.dumps(wanted, indent=2).replace("\n", "\n  "))
            return {}
    for k, v in wanted.items():
        if k == "recommendations":
            cur = data.setdefault(k, [])
            cur += [x for x in v if x not in cur]
        elif k == "configurations":
            cur = data.setdefault(k, [])
            names = {c.get("name") for c in cur}
            cur += [c for c in v if c["name"] not in names]
        else:
            data.setdefault(k, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=4) + "\n")
    return data


def write_vscode(repo: Path = REPO) -> None:
    vs = repo / ".vscode"
    interp = "${workspaceFolder}/.venv/Scripts/python.exe" if os.name == "nt" \
        else "${workspaceFolder}/.venv/bin/python"
    _merge(vs / "settings.json", {
        "python.defaultInterpreterPath": interp,
        "python.testing.pytestEnabled": True,
        "python.testing.unittestEnabled": False,
        "python.testing.pytestArgs": ["fem_checks/tests"],
        "jupyter.notebookFileRoot": "${workspaceFolder}/fem_checks",
        # The Problems tab otherwise fills with Pylance type warnings about pyNastran
        # (user asked how to make them go away). Real errors still show.
        "python.analysis.typeCheckingMode": "off",
        "files.exclude": {"**/__pycache__": True, "**/.pytest_cache": True},
    })
    _merge(vs / "extensions.json", {"recommendations": EXTENSIONS})
    _merge(vs / "launch.json", {"version": "0.2.0", "configurations": [
        {"name": "FEM checks: GUI", "type": "debugpy", "request": "launch",
         "module": "fem_checks", "cwd": "${workspaceFolder}", "justMyCode": True},
        {"name": "FEM checks: run a settings file", "type": "debugpy", "request": "launch",
         "module": "fem_checks", "args": ["${input:settingsFile}"], "cwd": "${workspaceFolder}"},
        {"name": "FEM checks: re-evaluate (no Nastran)", "type": "debugpy", "request": "launch",
         "module": "fem_checks", "args": ["${input:settingsFile}", "--no-run"],
         "cwd": "${workspaceFolder}"},
    ]})
    # The ${input:...} prompt used above.
    lp = vs / "launch.json"
    data = json.loads(lp.read_text())
    if not any(i.get("id") == "settingsFile" for i in data.get("inputs", [])):
        data.setdefault("inputs", []).append(
            {"id": "settingsFile", "type": "promptString",
             "description": "Path to fem_checks.json (in the run's output folder)"})
        lp.write_text(json.dumps(data, indent=4) + "\n")
    say(f"  wrote/merged {vs}")


def install_extensions() -> None:
    code = shutil.which("code") or shutil.which("code.cmd")
    if not code:
        say("  'code' is not on PATH: open the repo folder in VS Code and accept the "
            "'recommended extensions' prompt (Python, Jupyter).")
        return
    for ext in EXTENSIONS:
        subprocess.run([code, "--install-extension", ext, "--force"])


# ------------------------------------------------------------------ 5 Nastran
def find_nastran(globs=NASTRAN_GLOBS) -> list[str]:
    """nastran.exe candidates, newest install folder first.

    Only nastran.exe is returned. The same bin folder also holds nastranw.exe, a
    launcher that hands the job off and returns at once (exit code 0, no .f06 yet).
    The first user run picked it up because it sorts ahead of nastran.exe, and every
    check came back ERROR in under a second."""
    found = []
    for g in globs:
        for f in sorted(glob.glob(g), reverse=True):  # newest version folder first
            if Path(f).name.lower() == "nastran.exe" and f not in found:
                found.append(f)
    return found


def prefill_gui(nastran: str) -> None:
    """Put the Nastran path into the GUI's remembered settings if none is set yet."""
    from .gui_paths import LAST  # light module: no tkinter import needed here
    data = {}
    if LAST.exists():
        try:
            data = json.loads(LAST.read_text())
        except json.JSONDecodeError:
            data = {}
    if not data.get("nastran_exe"):
        data["nastran_exe"] = nastran
        LAST.write_text(json.dumps(data, indent=2))
        say(f"  GUI will start with Nastran = {nastran}")


# ------------------------------------------------------------------ 6 verify
def verify() -> list[str]:
    problems = []
    for mod in ("numpy", "scipy", "matplotlib", "pyNastran", "reportlab"):
        try:
            m = __import__(mod)
            say(f"  {mod:12s} {getattr(m, '__version__', getattr(m, 'Version', '?'))}")
        except Exception as exc:
            problems.append(f"{mod} does not import: {exc}")
    try:
        import tkinter  # noqa: F401
        say("  tkinter      OK")
    except Exception as exc:
        problems.append(f"tkinter missing ({exc}): reinstall Python 3.12 with 'tcl/tk and IDLE' "
                        "ticked, or use the command line (python -m fem_checks settings.json).")
    sample = REPO / "freefree_check-0000.f06"
    if sample.exists():
        try:
            from .f06 import parse_f06
            f = parse_f06(sample)
            say(f"  sample .f06 parsed: {len(f.modes)} modes, {len(f.groundcheck)} GROUNDCHECK sets")
        except Exception as exc:
            problems.append(f"parsing the sample .f06 failed: {exc}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fem_checks.setup_env")
    ap.add_argument("--no-dev", action="store_true", help="skip pytest/Jupyter")
    ap.add_argument("--no-vscode", action="store_true", help="don't touch .vscode/")
    ap.add_argument("--no-tests", action="store_true", help="don't run the test suite")
    a = ap.parse_args(argv)

    say("1. Python")
    ok, msg = python_ok()
    say("  " + msg)
    if not ok:
        return 2
    if sys.prefix == sys.base_prefix:
        say("  note: not running inside a virtual environment; packages go into this Python.")

    say("\n2. Packages")
    files = [PKG / "requirements.txt"] + ([] if a.no_dev else [PKG / "requirements-dev.txt"])
    if not pip_install(files):
        return 3

    if not a.no_dev:
        say("\n3. Jupyter kernel")
        say("  registered 'fem_checks (.venv)'" if register_kernel() else "  kernel registration failed (notebook still opens; pick the .venv interpreter by hand)")

    if not a.no_vscode:
        say("\n4. VS Code")
        write_vscode()
        install_extensions()

    say("\n5. Nastran")
    found = find_nastran()
    if found:
        for f in found:
            say(f"  found {f}")
        prefill_gui(found[0])
    else:
        say("  no nastran.exe in the usual Siemens folders; browse to it in the GUI.")

    say("\n6. Check")
    problems = verify()
    if not a.no_tests and not a.no_dev:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(PKG / "tests")], cwd=REPO)
        if r.returncode != 0:
            problems.append("test suite failed (output above)")
    if problems:
        say("\nSetup finished with problems:")
        for p in problems:
            say("  - " + p)
        return 1
    say("\nReady. Start the GUI with:  .venv\\Scripts\\python -m fem_checks"
        "\nor open the folder in VS Code and use Run > 'FEM checks: GUI'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
