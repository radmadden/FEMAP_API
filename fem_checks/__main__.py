"""Command line.

  python -m fem_checks                         open the GUI
  python -m fem_checks settings.json           run everything from a settings file
  python -m fem_checks settings.json --no-run  re-evaluate the outputs already in the
                                               output folder (no Nastran), rebuild the report
  python -m fem_checks --template out.json     write a settings file with all defaults
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .settings import Settings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fem_checks", description="FEM validation checks (Simcenter Nastran)")
    ap.add_argument("settings", nargs="?", help="settings JSON; omit to open the GUI")
    ap.add_argument("--no-run", action="store_true", help="don't run Nastran; use existing outputs")
    ap.add_argument("--template", metavar="PATH", help="write a default settings JSON and exit")
    a = ap.parse_args(argv)
    if a.template:
        Settings().to_json(Path(a.template))
        print(f"wrote {a.template}")
        return 0
    if not a.settings:
        from .gui import launch
        launch()
        return 0
    from .pipeline import run
    s = Settings.from_json(Path(a.settings))
    summary = run(s, print, run_solver=not a.no_run)
    if summary.error:
        print("ERROR:", summary.error)
        return 2
    return 0 if summary.overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
