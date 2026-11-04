"""Writes the five check decks. Each is: executive + case control + a small bulk section
with the check's own cards + INCLUDE of the shared model bulk data.

Case-control syntax is from the Simcenter QRG (qrg ~line) and, where possible, copied
from the reference decks in fem_checks/reference_model (freefree_check-0000.f06 case-control echo,
UD_check-template.dat, 1G_check_template.dat).

Reserved IDs (all offsets from ``base``, see model.reserved_base):
  base+1        SPC1 kinematic mount (123456 at the mount grid)
  base+11..16   SPCD unit displacement T1..T3, R1..R3
  base+21..23   GRAV 1g X, Y, Z
  base+31       TEMPD initial temperature 0.0  (TEMP(INIT))
  base+32       TEMPD load temperature 1.0     (TEMP(LOAD))  -> dT = 1
  base+41       EIGRL free-free
  base+42       EIGRL constrained
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# key -> (number, suffix). File name = "<nn>_<model stem>_<suffix>.dat", e.g. the model
# gimbal_assy_full.dat gives 01_gimbal_assy_full_freefree.dat (the check
# files carry the original model name for traceability).
DECK_NAMES = {
    "freefree": ("01", "freefree"),
    "constrained_modes": ("02", "constrained_modes"),
    "unit_disp": ("03", "unit_disp"),
    "gravity": ("04", "gravity"),
    "thermal": ("05", "thermal"),
}


def safe_stem(deck_path) -> str:
    """Model file name without extension, made safe for a Nastran job name: spaces and
    anything other than letters, digits, '-', '_' and '.' become '_'."""
    stem = Path(str(deck_path)).stem
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_") or "model"


def deck_filename(key: str, stem: str) -> str:
    nn, suffix = DECK_NAMES[key]
    return f"{nn}_{stem}_{suffix}.dat"


def bulk_filenames(stem: str) -> tuple[str, str]:
    """(bulk data, thermal bulk data) include-file names for this model."""
    return f"{stem}_bulk.bdf", f"{stem}_bulk_thermal.bdf"


@dataclass
class DeckIds:
    base: int

    @property
    def mount_spc(self):
        return self.base + 1

    def spcd(self, dof):
        return self.base + 10 + dof

    def grav(self, axis):  # axis 1..3
        return self.base + 20 + axis

    @property
    def temp_init(self):
        return self.base + 31

    @property
    def temp_load(self):
        return self.base + 32

    @property
    def eigrl_free(self):
        return self.base + 41

    @property
    def eigrl_con(self):
        return self.base + 42


def _cc(line: str, indent: str = "  ") -> str:
    """Wrap a case-control command at commas so no line passes column 72.
    Continuation is a trailing comma, as in the reference GROUNDCHECK echo."""
    if len(indent + line) <= 72:
        return indent + line
    parts, cur = [], indent
    for tok in line.split(","):
        piece = tok if cur.strip() == "" else "," + tok
        if len(cur + piece) > 71 and cur.strip():
            parts.append(cur + ",")
            cur = indent + "  " + tok
        else:
            cur += piece
    parts.append(cur)
    return "\n".join(parts)


def _trace_header(trace: dict) -> list[str]:
    """$-comment block naming where this deck came from (traceability)."""
    import datetime as _dt
    lines = ["$ Written by fem_checks (FEM validation). Regenerate instead of editing.",
             f"$ Written    : {_dt.datetime.now():%Y-%m-%d %H:%M}",
             f"$ Source deck: {trace.get('deck', '-')}"]
    if trace.get("femap_model"):
        lines.append(f"$ FEMAP model: {trace['femap_model']}  (exported {trace.get('femap_date', '-')})")
    if trace.get("title") or trace.get("subtitle"):
        lines.append(f"$ Model title: {trace.get('title', '')} / {trace.get('subtitle', '')}")
    return [x[:80] for x in lines]


def _deck(sol: int, check_name: str, global_cc: list, subcases: list, bulk: list,
          include: str, carried: list, trace: dict) -> str:
    """TITLE and SUBTITLE are the model deck's own (so every .f06 page says which model it
    is); the check name goes in LABEL. Subcase descriptions also use LABEL so they don't
    replace the model subtitle."""
    title = trace.get("title") or "FEM VALIDATION CHECKS"
    out = _trace_header(trace)
    out += trace.get("nastran_statements", [])
    out += [
        f"SOL {sol}",
        "CEND",
        _cc(f"TITLE = {title}"[:72]),
    ]
    if trace.get("subtitle"):
        out.append(_cc(f"SUBTITLE = {trace['subtitle']}"[:72]))
    out += [_cc(f"LABEL = {check_name}"[:72]), _cc("ECHO = NONE")]
    out += ["$ carried from the model deck (mass / MPC / DMIG selection)"] if carried else []
    out += [_cc(x) for x in carried]
    out += [_cc(x) for x in global_cc]
    for sc_id, lines in subcases:
        out.append(f"SUBCASE {sc_id}")
        out += [_cc(x, "    ") for x in lines]
    out.append("BEGIN BULK")
    out += bulk
    out.append(f"INCLUDE '{include}'")
    out.append("ENDDATA")
    return "\n".join(out) + "\n"


def write_decks(out_dir: Path, which: list[str], ids: DeckIds, mount: int,
                gravity_spc: int, real_spc: int, g: float, n_free: int, n_con: int,
                autospc_free: bool, carried: list, trace: dict | None = None,
                stem: str = "model") -> dict[str, Path]:
    """Write the decks named in ``which`` (keys of DECK_NAMES). Returns {key: path}.

    mount:       the kinematic-mount grid (auto-picked or entered in the settings). The SPC1 card is
                 "SPC1, <set id>, 123456, <mount>": ids.mount_spc is only the SET ID the
                 case control selects; the constrained GRID is always ``mount``.
    gravity_spc: SPC set for the gravity check (ids.mount_spc or the real set, design decision D2).
    real_spc:    the model's real boundary-condition set, used by constrained modes."""
    trace = trace or {}
    bulk_file, thermal_bulk_file = bulk_filenames(stem)
    out_dir = Path(out_dir)
    paths = {}
    mount_spc_card = f"SPC1,{ids.mount_spc},123456,{mount}"
    need_mount_card = gravity_spc == ids.mount_spc

    if "freefree" in which:
        sets = "G,N,N+AUTOSPC,F,A" if autospc_free else "G,N,F,A"
        cc = [
            # qrg ~18934: must be above the subcase level; GRID = reference point for rigid motion.
            f"GROUNDCHECK(PRINT,SET=({sets}),GRID={mount},DATAREC=YES)=YES",
            # qrg ~31138: MASS selects mass units (default WEIGHT); CGI=YES prints CG/inertia per set.
            f"WEIGHTCHECK(PRINT,SET=({sets}),GRID={mount},CGI=YES,MASS)=YES",
            f"METHOD = {ids.eigrl_free}",
            "DISPLACEMENT(PLOT) = ALL",
        ]
        bulk = [
            "PARAM,POST,-1",
            f"PARAM,AUTOSPC,{'YES' if autospc_free else 'NO'}",
            "PARAM,GRDPNT,0",
            f"EIGRL,{ids.eigrl_free},,,{n_free}",
        ]
        p = out_dir / deck_filename("freefree", stem)
        p.write_text(_deck(103, "FEM CHECK 01 FREE-FREE MODES + GROUNDCHECK", cc,
                           [(1, ["LABEL = FEM CHECK 01 FREE-FREE, NO SPC"])], bulk, bulk_file,
                           carried, trace))
        paths["freefree"] = p

    if "constrained_modes" in which:
        cc = [
            f"SPC = {real_spc}",
            f"METHOD = {ids.eigrl_con}",
            "DISPLACEMENT(PLOT) = ALL",
            # Same request as the reference free-free deck (case-control echo).
            "MEFFMASS(PLOT,PRINT,GRID=0,PARTFAC,MEFFM,FRACSUM,SUMMARY) = YES",
        ]
        bulk = ["PARAM,POST,-1", "PARAM,AUTOSPC,YES", "PARAM,GRDPNT,0",
                f"EIGRL,{ids.eigrl_con},,,{n_con}"]
        p = out_dir / deck_filename("constrained_modes", stem)
        p.write_text(_deck(103, "FEM CHECK 02 CONSTRAINED MODES", cc,
                           [(1, [f"LABEL = FEM CHECK 02 CONSTRAINED, SPC {real_spc}"])], bulk,
                           bulk_file, carried, trace))
        paths["constrained_modes"] = p

    if "unit_disp" in which:
        cc = [
            f"SPC = {ids.mount_spc}",
            "DISPLACEMENT(PLOT) = ALL",
            "SPCFORCE(PLOT) = ALL",
            "OLOAD(PLOT) = ALL",
            "FORCE(PLOT,CORNER) = ALL",
        ]
        names = ["T1", "T2", "T3", "R1", "R2", "R3"]
        subs = [(k, [f"LABEL = FEM CHECK 03 UNIT ENFORCED {names[k - 1]} AT GRID {mount}",
                     f"LOAD = {ids.spcd(k)}"]) for k in range(1, 7)]
        # qrg ~119845 remark 3: SPCD DOFs must also be on an SPC1 selected by SPC.
        bulk = ["PARAM,POST,-1", "PARAM,AUTOSPC,YES", mount_spc_card]
        bulk += [f"SPCD,{ids.spcd(k)},{mount},{k},1.0" for k in range(1, 7)]
        p = out_dir / deck_filename("unit_disp", stem)
        p.write_text(_deck(101, "FEM CHECK 03 UNIT ENFORCED DISPLACEMENT", cc, subs,
                           bulk, bulk_file, carried, trace))
        paths["unit_disp"] = p

    if "gravity" in which:
        cc = [
            f"SPC = {gravity_spc}",
            # SOL 101 only allows SET=G (qrg ~31195).
            "WEIGHTCHECK(PRINT,SET=(G),CGI=YES,MASS)=YES",
            "DISPLACEMENT(PLOT) = ALL",
            "SPCFORCE(PLOT) = ALL",
            "OLOAD(PLOT) = ALL",
        ]
        axes = ["X", "Y", "Z"]
        subs = [(k, [f"LABEL = FEM CHECK 04 UNIT GRAVITY {axes[k - 1]}", f"LOAD = {ids.grav(k)}"])
                for k in range(1, 4)]
        bulk = ["PARAM,POST,-1", "PARAM,AUTOSPC,YES", "PARAM,GRDPNT,0"]
        vec = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
        bulk += [f"GRAV,{ids.grav(k)},0,{g:.6g},{v[0]},{v[1]},{v[2]}" for k, v in zip(range(1, 4), vec)]
        if need_mount_card:
            bulk.append(mount_spc_card)
        p = out_dir / deck_filename("gravity", stem)
        p.write_text(_deck(101, "FEM CHECK 04 UNIT GRAVITY", cc, subs, bulk, bulk_file,
                           carried, trace))
        paths["gravity"] = p

    if "thermal" in which:
        cc = [
            # qrg ~26012: LAGRAN lets rigid elements expand with their ALPHA (design decision D4).
            "RIGID = LAGRAN",
            f"SPC = {ids.mount_spc}",
            # qrg ~29940 remark 8: with TEMP(INIT) defined, strain = alpha*(T_load - T_init).
            f"TEMPERATURE(INITIAL) = {ids.temp_init}",
            "DISPLACEMENT(PLOT) = ALL",
            "SPCFORCE(PLOT) = ALL",
            "STRESS(PLOT) = ALL",
        ]
        subs = [(1, ["LABEL = FEM CHECK 05 UNIFORM +1 DEGREE SOAK",
                     f"TEMPERATURE(LOAD) = {ids.temp_load}"])]
        bulk = ["PARAM,POST,-1", "PARAM,AUTOSPC,YES", mount_spc_card,
                f"TEMPD,{ids.temp_init},0.0", f"TEMPD,{ids.temp_load},1.0"]
        p = out_dir / deck_filename("thermal", stem)
        p.write_text(_deck(101, "FEM CHECK 05 THERMAL SOAK", cc, subs, bulk,
                           thermal_bulk_file, carried, trace))
        paths["thermal"] = p
    return paths
