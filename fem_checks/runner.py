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


def solver_exe(exe: str, log: Callable[[str], None] = print) -> str:
    """Return the executable that runs a job in the foreground.

    nastranw.exe (next to nastran.exe in the Siemens bin folder) is a launcher: it
    returns immediately with exit code 0 while the job runs, or doesn't, elsewhere.
    An early live run used it and got "no .f06 written" in under a second for every
    deck. If it's selected and nastran.exe sits beside it, use nastran.exe instead."""
    p = Path(exe)
    if p.name.lower() == "nastranw.exe":
        alt = p.with_name("nastran.exe")
        if alt.is_file():
            log(f"  note: {p.name} is a launcher that doesn't wait for the job; using {alt.name}")
            return str(alt)
        log(f"  WARNING: {p.name} returns before the job finishes; select nastran.exe instead")
    return str(exe)


def run_nastran(exe: str, deck: Path, args: list, timeout: Optional[float] = None,
                log: Callable[[str], None] = print) -> RunResult:
    deck = Path(deck)
    exe = solver_exe(exe, log)
    clear_outputs(deck)
    cmd = [str(exe), deck.name, *args]
    log(f"  running: {' '.join(cmd)}  (in {deck.parent})")
    t0 = time.time()
    rc, err, console = None, None, ""
    try:
        proc = subprocess.run(cmd, cwd=str(deck.parent), capture_output=True, text=True,
                              errors="replace", timeout=timeout)
        rc = proc.returncode
        console = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        err = f"Nastran did not finish within {timeout} s"
    except OSError as exc:
        err = f"could not start Nastran: {exc}"
    dt = time.time() - t0
    f06 = _find(deck.parent, deck.stem, ".f06")
    op2 = _find(deck.parent, deck.stem, ".op2")
    if err is None and f06 is None:
        err = f"no .f06 written (return code {rc}); see {deck.stem}.f04/.log"
        # Whatever Nastran printed is usually the best clue (license, path, bad keyword).
        tail = [x for x in console.strip().splitlines() if x.strip()][-15:]
        if tail:
            log("  Nastran console output (last lines):")
            for x in tail:
                log("    " + x)
        else:
            log("  Nastran printed nothing to the console.")
    return RunResult(str(deck), str(f06) if f06 else None, str(op2) if op2 else None,
                     rc, dt, err)
