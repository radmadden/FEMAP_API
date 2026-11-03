"""Runs the local Simcenter Nastran on one deck and collects its output files.

Command: <nastran.exe> <deck> scr=yes old=no batch=no
  scr=yes   scratch files deleted at the end
  old=no    overwrite instead of versioning old output
  batch=no  run in the foreground so this call waits for the job to finish
The job runs with the deck's folder as the working directory, so .f06/.op2 land next
to the deck.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

OUTPUT_SUFFIXES = (".f06", ".op2", ".f04", ".log")


@dataclass
class RunResult:
    deck: str
    f06: Optional[str]
    op2: Optional[str]
    returncode: Optional[int]
    seconds: float
    error: Optional[str]          # None when Nastran finished and wrote an .f06

    @property
    def ok(self) -> bool:
        return self.error is None


def _find(folder: Path, stem: str, suffix: str) -> Optional[Path]:
    """Nastran on Windows may change the case of output names; match case-insensitively."""
    want = (stem + suffix).lower()
    for p in folder.iterdir():
        if p.name.lower() == want:
            return p
    return None


def clear_outputs(deck: Path) -> list[str]:
    """Delete this deck's old .f06/.op2/.f04/.log so a failed run can never be parsed as
    if it were the new one. Only files named after the deck the tool wrote are touched."""
    removed = []
    for suf in OUTPUT_SUFFIXES:
        p = _find(deck.parent, deck.stem, suf)
        if p is not None:
            p.unlink()
            removed.append(p.name)
    return removed


def run_nastran(exe: str, deck: Path, args: list, timeout: Optional[float] = None,
                log: Callable[[str], None] = print) -> RunResult:
    deck = Path(deck)
    clear_outputs(deck)
    cmd = [str(exe), deck.name, *args]
    log(f"  running: {' '.join(cmd)}  (in {deck.parent})")
    t0 = time.time()
    rc, err = None, None
    try:
        proc = subprocess.run(cmd, cwd=str(deck.parent), capture_output=True, text=True,
                              timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        err = f"Nastran did not finish within {timeout} s"
    except OSError as exc:
        err = f"could not start Nastran: {exc}"
    dt = time.time() - t0
    f06 = _find(deck.parent, deck.stem, ".f06")
    op2 = _find(deck.parent, deck.stem, ".op2")
    if err is None and f06 is None:
        err = f"no .f06 written (return code {rc}); see {deck.stem}.f04/.log"
    return RunResult(str(deck), str(f06) if f06 else None, str(op2) if op2 else None,
                     rc, dt, err)
