"""Pass/fail evaluation for each check. Inputs are parsed .f06 data (f06.py), OP2
fields (results.py) and the model (model.py); outputs are CheckResult records that the
report and results.json are built from.

Status meanings:
  PASS     every criterion met
  FAIL     at least one criterion missed (the summary says which)
  INFO     report-only check (constrained modes, model summary)
  ERROR    the check could not be evaluated (Nastran FATAL, missing output, ...)
  SKIPPED  not requested / nothing to compare (e.g. no mass targets entered)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import results as R
from .f06 import F06, MassBlock

DOF_NAMES = ["T1", "T2", "T3", "R1", "R2", "R3"]


@dataclass
class Table:
    title: str
    headers: list
    rows: list
    note: str = ""
    # Cells to shade: (row, first_col, last_col, key), rows/cols 0-based over ``rows``.
    # Keys (colors in report.HIGHLIGHT): "pair" = the values a criterion compares with
    # each other (same shade = compared together), "group_a"/"group_b" = row groups
    # (e.g. rigid-body vs elastic modes), "fail" = a value beyond its limit.
    highlight: list = field(default_factory=list)


@dataclass
class CheckResult:
    key: str
    title: str
    status: str
    summary: str
    details: list = field(default_factory=list)   # bullet lines for the report
    tables: list = field(default_factory=list)    # Table records
    data: dict = field(default_factory=dict)      # numbers for charts + results.json
    excerpts: list = field(default_factory=list)  # (title, [lines]) verbatim .f06 text


def _e(x, fmt="{:.3e}"):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return fmt.format(x)


def error_result(key, title, why) -> CheckResult:
    return CheckResult(key, title, "ERROR", why)


def _fatal_or_missing(f06: Optional[F06], key, title) -> Optional[CheckResult]:
    if f06 is None:
        return error_result(key, title, "Nastran did not produce an .f06 for this check.")
    if f06.fatals:
        return error_result(key, title, f"Nastran FATAL: {f06.fatals[0].text[:200]}")
    if not f06.completed:
        return error_result(key, title, "The .f06 has no END OF JOB; the run stopped early.")
    return None


def _epsilon_check(f06: F06, limit: float) -> tuple[bool, list, list]:
    rows, bad = [], []
    for e in f06.epsilon:
        flag = e["flagged"] or abs(e["epsilon"]) > limit
        rows.append([e["seq"], _e(e["epsilon"]), _e(e["external_work"]), "FAIL" if flag else "ok"])
        if flag:
            bad.append(e["seq"])
    return (not bad), rows, bad


# =============================================================== free-free
def check_freefree(f06: Optional[F06], th, autospc: bool) -> CheckResult:
    key, title = "freefree", "Free-free modes + GROUNDCHECK"
    bad = _fatal_or_missing(f06, key, title)
    if bad:
        return bad
    modes = sorted(f06.modes, key=lambda m: m.mode)
    if len(modes) < 7:
        return error_result(key, title, f"Only {len(modes)} modes found; need at least 7 "
                                        "(6 rigid-body + first elastic).")
    hz = [abs(m.cycles) for m in modes]
    f_rbm = max(hz[:6])
    f7 = hz[6]
    ratio = f_rbm / f7 if f7 > 0 else float("inf")
    ratio_ok = ratio <= th.rbm_ratio

    gc = f06.groundcheck
    gc_ok = bool(gc) and all(s.all_pass for s in gc)
    failed_sets = [s.set_name for s in gc if not s.all_pass]

    wc_changes = []
    for b in f06.mass_blocks:
        if b.source == "WEIGHTCHECK" and b.change_pct and any(abs(c) > 0 for c in b.change_pct
                                                              if not math.isnan(c)):
            wc_changes.append((b.dof_set, b.change_pct))

    status = "PASS" if (ratio_ok and gc_ok) else "FAIL"
    why = []
    if not ratio_ok:
        why.append(f"rigid/elastic ratio {ratio:.2e} > {th.rbm_ratio:.0e}")
    if not gc:
        why.append("no GROUNDCHECK output found")
    elif failed_sets:
        why.append("GROUNDCHECK FAIL in set(s) " + ", ".join(failed_sets))
    summary = (f"max rigid-body {f_rbm:.2e} Hz, first elastic {f7:.4g} Hz, ratio {ratio:.1e}; "
               f"GROUNDCHECK {'all sets PASS' if gc_ok else 'FAIL'}") if status == "PASS" \
        else "; ".join(why)

    details = [
        f"Criterion: max(f1..f6) / f7 <= {th.rbm_ratio:.0e} (decision #1). Result {ratio:.2e}.",
        "FEMCI's absolute 1e-4 Hz limit (femci ~2095) is deliberately not applied: it doesn't "
        "scale with model size or units, and the 6th/7th separation is what shows grounding.",
        "GROUNDCHECK passes only if every requested set (G, N"
        + (", N+AUTOSPC" if autospc else "") + ", F, A) prints PASS in all 6 directions.",
        f"Free-free run used PARAM,AUTOSPC,{'YES' if autospc else 'NO'}. FEMCI (~2127): AUTOSPC "
        "can make the A-set fail without real grounding.",
    ]
    if wc_changes:
        details.append("WEIGHTCHECK: rigid-body mass changed during reduction in set(s) "
                       + ", ".join(f"{s} ({max(abs(c) for c in ch):g}%)" for s, ch in wc_changes)
                       + ". MPCs/RBEs are losing or adding mass.")
    else:
        details.append("WEIGHTCHECK: 0% rigid-body mass change in every reduced set.")
    tables = [eigen_table(modes, rigid=6), groundcheck_table(gc)]
    data = dict(freq_hz=hz, ratio=ratio, f_rbm_max=f_rbm, f7=f7,
                groundcheck={s.set_name: dict(limit=s.limit, se=s.strain_energy, passed=s.passed)
                             for s in gc},
                weightcheck_change={s: c for s, c in wc_changes})
    ex = f06.excerpts
    excerpts = ([("GROUNDCHECK, all DOF sets", _join(ex.get("groundcheck", [])))] if ex.get("groundcheck") else []) \
        + ([("Real eigenvalues", _join(ex.get("eigenvalues", [])))] if ex.get("eigenvalues") else []) \
        + ([("WEIGHTCHECK, all DOF sets", _join(ex.get("mass", [])))] if ex.get("mass") else [])
    return CheckResult(key, title, status, summary, details, tables, data, excerpts)


def _join(blocks: list) -> list:
    """Several verbatim blocks -> one list of lines with a blank line between blocks."""
    out = []
    for b in blocks:
        if out:
            out.append("")
        out += b
    return out


def eigen_table(modes, rigid: int = 0) -> Table:
    """The REAL EIGENVALUES table with every column Nastran prints. With rigid=6 the
    rigid-body rows and the first elastic row are shaded as groups, and the two
    frequencies the free-free criterion divides (max of modes 1-6, mode 7) as a pair."""
    rows = [[m.mode, m.order, _e(m.eigenvalue), _e(m.radians), _e(abs(m.cycles), "{:.4e}"),
             _e(m.gen_mass), _e(m.gen_stiffness)]
            + (["rigid-body" if i < rigid else "elastic"] if rigid else [])
            for i, m in enumerate(modes)]
    hl, note = [], ""
    if rigid and len(modes) > rigid:
        last = len(rows[0]) - 1
        hl += [(i, 0, last, "group_a") for i in range(rigid)] + [(rigid, 0, last, "group_b")]
        i_max = max(range(rigid), key=lambda i: abs(modes[i].cycles))
        hl += [(i_max, 4, 4, "pair"), (rigid, 4, 4, "pair")]
        note = ("Shading: blue rows = rigid-body modes 1-6, orange row = first elastic mode; the two "
                "darker CYCLES cells are the pair the criterion divides (max rigid-body / first elastic).")
    headers = ["Mode", "Extraction order", "Eigenvalue", "Radians", "Cycles (Hz)",
               "Generalized mass", "Generalized stiffness"] + (["Class"] if rigid else [])
    return Table("Real eigenvalues (all columns as printed by Nastran)", headers, rows, note, hl)


def groundcheck_table(gc) -> Table:
    """GROUNDCHECK laid out like the .f06: one column pair (strain energy, PASS/FAIL) per
    DOF set, directions 1-6 as rows, the set's limit in the last row. FAIL cells shaded."""
    headers = ["Direction"]
    for s in gc:
        headers += [f"{s.set_name}-SET ({s.matrix}) strain energy", "PASS/FAIL"]
    rows, hl = [], []
    for d in range(6):
        row = [d + 1]
        for k, s in enumerate(gc):
            se = s.strain_energy[d] if d < len(s.strain_energy) else None
            ok = s.passed[d] if d < len(s.passed) else False
            row += [_e(se, "{:.6E}"), "PASS" if ok else "FAIL"]
            if not ok:
                hl.append((d, 1 + 2 * k, 2 + 2 * k, "fail"))
        rows.append(row)
    rows.append(["Limit"] + sum(([_e(s.limit, "{:.6E}"), ""] for s in gc), []))
    return Table("GROUNDCHECK: rigid-body strain energy per DOF set (Nastran's PASS/FAIL)",
                 headers, rows, "Limit = Nastran's default: largest stiffness term / 1e10 "
                 "(qrg ~18934). Sets in Nastran's order G, N, N+AUTOSPC, F, A.", hl)


def resultant_table(title: str, oload, spcf, load_dir: Optional[int], scale_note: str = "") -> Table:
    """OLOAD and SPCFORCE TOTALS side by side, one row per component, plus their sum.
    Rows where the applied load is significant are shaded as compared pairs; the load
    direction row is the darker 'pair' shade."""
    names = DOF_NAMES
    o = list(oload) if oload else [None] * 6
    s = list(spcf) if spcf else [None] * 6
    big = max([abs(x) for x in o[:3] if x is not None] + [0.0])
    bigm = max([abs(x) for x in o[3:] if x is not None] + [0.0])
    rows, hl = [], []
    for k in range(6):
        tot = (o[k] + s[k]) if (o[k] is not None and s[k] is not None) else None
        rows.append([names[k], _e(o[k], "{:.6E}"), _e(s[k], "{:.6E}"), _e(tot, "{:.3E}")])
        ref = big if k < 3 else bigm
        if load_dir is not None and k == load_dir:
            hl.append((k, 1, 2, "pair"))
        elif o[k] is not None and ref and abs(o[k]) > 1e-6 * ref:
            hl.append((k, 1, 2, "group_a"))
    note = ("Same-shade cells are compared with each other: OLOAD and SPCFORCE should be equal "
            "and opposite, so OLOAD + SPCFORCE should be ~0. Darker = load direction; lighter = other "
            "components with significant applied load (moments about the basic origin). " + scale_note).strip()
    return Table(title, ["Component", "OLOAD TOTAL", "SPCFORCE TOTAL", "OLOAD + SPCFORCE"], rows, note, hl)


# =========================================================== constrained modes
def check_constrained(f06: Optional[F06], spc_label: str) -> CheckResult:
    key, title = "constrained_modes", "Constrained modes"
    bad = _fatal_or_missing(f06, key, title)
    if bad:
        return bad
    modes = sorted(f06.modes, key=lambda m: m.mode)
    if not modes:
        return error_result(key, title, "No eigenvalues found in the .f06.")
    fr = {r.mode: r for r in f06.meff_fractions}
    dirs = DOF_NAMES
    rows = []
    for m in modes:
        r = fr.get(m.mode)
        rows.append([m.mode, _e(abs(m.cycles), "{:.4g}")]
                    + [(_e(r.fraction.get(d), "{:.3f}") if r else "-") for d in dirs])
    cum_rows = []
    if f06.meff_fractions:
        last = f06.meff_fractions[-1]
        cum_rows = [["Cumulative (all modes found)"] + [_e(last.cumsum.get(d), "{:.3f}") for d in dirs]]
    n_low = sum(1 for m in modes if abs(m.cycles) < 1.0)
    summary = (f"{len(modes)} modes, first {abs(modes[0].cycles):.4g} Hz on {spc_label}; "
               "report only (no floor, decision Q4)")
    details = ["No pass/fail: gimbals can legitimately have near-zero bearing modes."]
    if n_low:
        details.append(f"{n_low} mode(s) below 1 Hz. Confirm they are intended mechanisms "
                       "(e.g. unlocked bearing rotations).")
    tables = [eigen_table(modes),
              Table("Effective-mass fraction per mode", ["Mode", "Freq (Hz)"] + dirs, rows)]
    if cum_rows:
        tables.append(Table("Cumulative effective-mass fraction", ["", *dirs], cum_rows))
    ex = f06.excerpts
    excerpts = ([("Real eigenvalues", _join(ex["eigenvalues"]))] if ex.get("eigenvalues") else []) \
        + ([("Modal effective mass fraction (MEFFMASS)", _join(ex["meff"]))] if ex.get("meff") else [])
    data = dict(freq_hz=[abs(m.cycles) for m in modes],
                fractions={d: [fr[m.mode].fraction.get(d, 0.0) if m.mode in fr else 0.0
                               for m in modes] for d in dirs},
                cumulative={d: (f06.meff_fractions[-1].cumsum.get(d) if f06.meff_fractions else None)
                            for d in dirs})
    return CheckResult(key, title, "INFO", summary, details, tables, data, excerpts)


# ===================================================== unit enforced displacement
def check_unit_disp(f06: Optional[F06], fields: dict, force_peaks: dict, mount_xyz,
                    rotation_grids: set, th, force_tol: float, moment_tol: float,
                    units: dict) -> CheckResult:
    """fields: {subcase: results.Field}; force_peaks: {subcase: element_force_peaks()}."""
    key, title = "unit_disp", "Unit enforced displacement / rotation"
    bad = _fatal_or_missing(f06, key, title)
    if bad:
        return bad
    rm = np.asarray(mount_xyz, dtype=float)
    fu, mu = units["force"], units["moment"]
    react_rows, field_rows, force_rows, corner_rows = [], [], [], []
    fails = []
    worst_grids = {}
    for sc in range(1, 7):
        name = DOF_NAMES[sc - 1]
        # (a) reaction at the mount: SPCFORCE resultant (about basic origin) moved to the mount.
        tot = f06.spcforce.get(sc)
        if tot is None:
            fails.append(f"{name}: no SPCFORCE resultant")
            react_rows.append([name, "-", "-", "missing"])
        else:
            F = np.array(tot[:3])
            M0 = np.array(tot[3:])
            Mm = M0 - np.cross(rm, F)
            fF, fM = float(np.linalg.norm(F)), float(np.linalg.norm(Mm))
            ok = fF <= force_tol and fM <= moment_tol
            react_rows.append([name, _e(fF), _e(fM), "ok" if ok else "FAIL"])
            if not ok:
                fails.append(f"{name}: mount reaction |F|={fF:.2e} {fu}, |M|={fM:.2e} {mu}")
        # (b) displacement field vs rigid-body motion.
        f = fields.get(sc)
        if f is None or len(f.nids) == 0:
            fails.append(f"{name}: no displacements in the OP2 for this model's grids")
            field_rows.append([name, "-", "-", "-", "missing"])
        else:
            t_exp, r_exp = R.rigid_body_field(f.xyz, rm, sc)
            err = R.field_error(f, t_exp, r_exp, rotation_grids)
            ok = err["t_err"] <= th.ud_disp_rel and (math.isnan(err["r_err"]) or err["r_err"] <= th.ud_disp_rel)
            field_rows.append([name, _e(err["t_err"]), _e(err["r_err"]), err["n_rot_grids"],
                               "ok" if ok else "FAIL"])
            worst_grids[name] = err["worst_t"][:5]
            if not ok:
                fails.append(f"{name}: displacement field error t={err['t_err']:.2e}, r={err['r_err']:.2e}")
        # (c) element forces (centroid rows).
        peaks = force_peaks.get(sc, {})
        for et, rec in sorted(peaks.items()):
            ok = rec["force"] <= force_tol and rec["moment"] <= moment_tol
            per = "/length" if rec["per_length"] else ""
            force_rows.append([name, et, rec["n"], _e(rec["force"]), _e(rec["moment"]),
                               (rec["worst_force"][0][0] if rec["worst_force"] else "-"),
                               "ok" if ok else "FAIL"])
            if not ok:
                fails.append(f"{name}: {et} centroid force {rec['force']:.2e} {fu}{per} / "
                             f"moment {rec['moment']:.2e} {mu}{per}")
            if "corner_force" in rec and (rec["corner_force"] > force_tol or rec.get("corner_moment", 0) > moment_tol):
                corner_rows.append([name, et, _e(rec["corner_force"]), _e(rec.get("corner_moment", 0.0)),
                                    ", ".join(str(e) for e, _ in rec["worst_corner_force"][:6])])
    eps_ok, eps_rows, eps_bad = _epsilon_check(f06, th.epsilon_max)
    if not eps_ok:
        fails.append(f"EPSILON flagged in load sequence(s) {eps_bad}")
    status = "FAIL" if fails else "PASS"
    summary = "; ".join(fails[:3]) + (" ..." if len(fails) > 3 else "") if fails else \
        f"6 subcases rigid: reactions, displacement field (<= {th.ud_disp_rel:.0e}) and element forces within limits"
    details = [
        f"Limits: reaction and element force <= {force_tol:.3g} {fu}, moment <= {moment_tol:.3g} {mu} "
        "(FEMCI 0.1 lb / 1.0 in-lb, femci ~1965, converted). Shell forces are per unit length.",
        f"Displacement field: max |u - u_rigid| / max |u_rigid| <= {th.ud_disp_rel:.0e}; rotations "
        "checked only on grids with rotational stiffness (shells, bars, beams, bushes, RBE2 independents).",
        "Element forces are judged at element centroids. CQUAD4 corner (bilinear) values are listed "
        "separately: on the user's model they reach 20-80 lbf/in under a 1 rad rigid rotation while "
        "centroid values are ~1e-7, so they are a recovery artifact, not grounding.",
    ]
    tables = [
        Table("Mount reaction (SPCFORCE resultant moved to the mount)", ["Subcase", f"|F| ({fu})", f"|M| ({mu})", "Result"], react_rows),
        Table("Displacement field vs rigid-body motion", ["Subcase", "Translation err", "Rotation err", "Rot. grids", "Result"], field_rows),
        Table("Peak element forces (centroid)", ["Subcase", "Type", "N", "Max force", "Max moment", "Worst elem", "Result"], force_rows),
    ]
    # Full data: the mount grid's own displacement in every subcase (should be the unit
    # vector of the enforced DOF; the diagonal is shaded) and every SPCFORCE total.
    mrows, mhl = [], []
    for sc in range(1, 7):
        f = fields.get(sc)
        if f is None or len(f.nids) == 0:
            continue
        i = int(np.argmin(np.linalg.norm(f.xyz - rm, axis=1)))
        mrows.append([DOF_NAMES[sc - 1], f.nids[i]] + [_e(v, "{:.6E}") for v in list(f.t[i]) + list(f.r[i])])
        mhl.append((len(mrows) - 1, 1 + sc, 1 + sc, "pair"))
    if mrows:
        tables.insert(0, Table("Displacement of the mount grid (basic), one row per subcase",
                               ["Subcase", "Grid"] + DOF_NAMES, mrows,
                               "Shaded diagonal = the enforced DOF; it must read 1.0 and every other "
                               "entry 0.", mhl))
    srows = [[DOF_NAMES[sc - 1]] + [_e(v, "{:.6E}") for v in f06.spcforce.get(sc, [None] * 6)]
             for sc in range(1, 7)]
    tables.insert(1, Table("SPCFORCE resultant TOTALS per subcase (about the basic origin)",
                           ["Subcase"] + DOF_NAMES, srows,
                           "All entries should be ~0: the model is only being moved rigidly."))
    if corner_rows:
        tables.append(Table("CQUAD4/plate CORNER values above the limit (INFO, not pass/fail)",
                            ["Subcase", "Type", "Max corner force", "Max corner moment", "Worst elements"], corner_rows))
    if eps_rows:
        tables.append(Table("EPSILON (UIM 5293)", ["Seq", "Epsilon", "External work", "Result"], eps_rows))
    data = dict(reactions=react_rows, field=field_rows, worst_grids=worst_grids,
                force_peaks={DOF_NAMES[sc - 1]: v for sc, v in force_peaks.items()})
    ex = f06.excerpts
    excerpts = [(t, _join(ex[k])) for t, k in (("SPCFORCE resultant", "spcforce"),
                                               ("OLOAD resultant", "oload"),
                                               ("EPSILON", "epsilon")) if ex.get(k)]
    return CheckResult(key, title, status, summary, details, tables, data, excerpts)


# ===================================================================== gravity
def check_gravity(f06: Optional[F06], mass: Optional[float], g: float, size: float, th,
                  units: dict, spc_label: str) -> CheckResult:
    key, title = "gravity", "Unit gravity (OLOAD vs SPCFORCE)"
    bad = _fatal_or_missing(f06, key, title)
    if bad:
        return bad
    rows, fails = [], []
    axes = ["X", "Y", "Z"]
    for sc in range(1, 4):
        o, s = f06.oload.get(sc), f06.spcforce.get(sc)
        if o is None or s is None:
            fails.append(f"{axes[sc - 1]}: resultant table missing")
            rows.append([axes[sc - 1]] + ["-"] * 6 + ["missing"])
            continue
        Fo, Mo = np.array(o[:3]), np.array(o[3:])
        Fs, Ms = np.array(s[:3]), np.array(s[3:])
        nFo = float(np.linalg.norm(Fo))
        errF = float(np.linalg.norm(Fo + Fs) / nFo) if nFo else float("inf")
        errM = float(np.linalg.norm(Mo + Ms) / (np.linalg.norm(Mo) + nFo * size)) if nFo else float("inf")
        if mass:
            errW = abs(nFo - mass * g) / (mass * g)
        else:
            errW = float("nan")
        ok = errF <= th.gravity_rel and errM <= th.gravity_rel and (math.isnan(errW) or errW <= th.gravity_rel)
        rows.append([axes[sc - 1], _e(Fo[sc - 1], "{:.6g}"), _e(Fs[sc - 1], "{:.6g}"),
                     _e(mass * g if mass else None, "{:.6g}"), _e(errF), _e(errM), _e(errW),
                     "ok" if ok else "FAIL"])
        if not ok:
            fails.append(f"{axes[sc - 1]}: force err {errF:.1e}, moment err {errM:.1e}, weight err {errW:.1e}")
    eps_ok, eps_rows, eps_bad = _epsilon_check(f06, th.epsilon_max)
    if not eps_ok:
        fails.append(f"EPSILON flagged in load sequence(s) {eps_bad}")
    status = "FAIL" if fails else "PASS"
    summary = "; ".join(fails) if fails else \
        f"OLOAD = -SPCFORCE and = mass*g to <= {th.gravity_rel:.0e} in X, Y, Z ({spc_label})"
    details = [
        f"g = {g:g} {units['length']}/s^2; mass from WEIGHTCHECK(MASS) = {_e(mass, '{:.6g}')}.",
        "Force error = |F_OLOAD + F_SPC| / |F_OLOAD|. Moment error = |M_OLOAD + M_SPC| / "
        "(|M_OLOAD| + |F_OLOAD| L), L = model bounding-box diagonal "
        f"({size:.4g} {units['length']}). Vector norms, because off-axis components are "
        "round-off noise that a per-component % test would fail on.",
        f"Mount: {spc_label}.",
    ]
    tables = [Table("Summary: load direction and errors",
                    ["Dir", f"OLOAD ({units['force']})", f"SPCFORCE ({units['force']})", "mass*g",
                     "Force err", "Moment err", "Weight err", "Result"], rows,
                    "Shaded: OLOAD, SPCFORCE and mass*g in the load direction are the three values "
                    "compared (they must agree in magnitude).",
                    [(i, 1, 3, "pair") for i in range(len(rows))])]
    for sc in range(1, 4):
        tables.append(resultant_table(
            f"Subcase {sc}: 1g {axes[sc - 1]}: OLOAD vs SPCFORCE, every component",
            f06.oload.get(sc), f06.spcforce.get(sc), sc - 1,
            f"mass*g = {_e(mass * g if mass else None, '{:.6E}')} {units['force']}."))
    if eps_rows:
        tables.append(Table("EPSILON (UIM 5293)", ["Seq", "Epsilon", "External work", "Result"], eps_rows))
    ex = f06.excerpts
    excerpts = [(t, _join(ex[k])) for t, k in (("OLOAD resultant", "oload"),
                                               ("SPCFORCE resultant", "spcforce"),
                                               ("WEIGHTCHECK (G-set, mass units)", "mass"),
                                               ("EPSILON", "epsilon")) if ex.get(k)]
    return CheckResult(key, title, status, summary, details, tables,
                       dict(rows=rows, mass=mass, g=g), excerpts)


# ===================================================================== thermal
def check_thermal(f06: Optional[F06], field: Optional[R.Field], stresses: list, mount_xyz,
                  alpha: float, e_max: float, th, neighbors: dict, model,
                  units: dict, lagran_bad: list) -> CheckResult:
    key, title = "thermal", "Thermal soak (+1 deg, uniform CTE, kinematic mount)"
    if lagran_bad:
        return error_result(key, title, f"{len(lagran_bad)} rigid element IDs exceed 1e8, which "
                                        f"RIGID=LAGRAN does not allow (qrg ~26012): {lagran_bad[:10]}")
    bad = _fatal_or_missing(f06, key, title)
    if bad:
        return bad
    if field is None or len(field.nids) == 0:
        return error_result(key, title, "No displacements in the thermal OP2 for this model's grids.")
    dT = 1.0
    rm = np.asarray(mount_xyz, dtype=float)
    t_exp, r_exp = R.thermal_field(field.xyz, rm, alpha, dT)
    err = R.field_error(field, t_exp, r_exp, None)
    # Rotations should be zero; normalize them by strain alpha*dT (rad per unit strain).
    r_max = float(np.linalg.norm(field.r, axis=1).max()) if len(field.r) else 0.0
    sig_scale = e_max * alpha * dT
    peak = stresses[0][2] if stresses else 0.0
    s_ratio = peak / sig_scale if sig_scale else float("nan")
    fails = []
    if err["t_err"] > th.thermal_disp_rel:
        fails.append(f"displacement error {err['t_err']:.2e} > {th.thermal_disp_rel:.0e}")
    if not stresses:
        fails.append("no stress output found")
    elif s_ratio > th.thermal_stress_rel:
        fails.append(f"stress ratio {s_ratio:.2e} > {th.thermal_stress_rel:.0e}")
    eps_ok, eps_rows, eps_bad = _epsilon_check(f06, th.epsilon_max)
    if not eps_ok:
        fails.append(f"EPSILON flagged in load sequence(s) {eps_bad}")
    tot = f06.spcforce.get(1)
    react = (float(np.linalg.norm(tot[:3])), float(np.linalg.norm(np.array(tot[3:]) - np.cross(rm, tot[:3])))) if tot else (None, None)
    status = "FAIL" if fails else "PASS"
    summary = "; ".join(fails) if fails else \
        f"expands freely: displacement error {err['t_err']:.1e}, max stress / (E alpha dT) {s_ratio:.1e}"
    hot_rows = []
    for eid, etype, val in stresses[:20]:
        elem = model.elements.get(eid)
        touch = set()
        if elem is not None:
            for n in elem.node_ids:
                touch.update(neighbors.get(n, []))
        hot_rows.append([eid, etype, _e(val), _e(val / sig_scale if sig_scale else None),
                         ", ".join(sorted(touch)[:4]) or "-"])
    details = [
        f"Every MAT CTE set to {alpha:g}, TREF 0; rigid elements given ALPHA {alpha:g} with "
        "RIGID=LAGRAN (decision #4); TEMP(INIT)=0, TEMP(LOAD)=1 so dT = 1 (qrg ~29940).",
        "Theory: u = alpha dT (r - r_mount), no rotation, zero stress (exact for any element mix "
        "when alpha is uniform). Replaces the Book's THDEF program (femci ~2285).",
        f"Stress scale E_max alpha dT = {sig_scale:.4g} (E_max = {e_max:.4g}). Max rotation "
        f"{r_max:.2e} rad (expect ~0).",
        "A PASS here assumes your production thermal runs also use RIGID=LAGRAN with ALPHA on "
        "rigid elements; under the default RIGID=LINEAR rigid elements do not expand.",
    ]
    if react[0] is not None:
        details.append(f"Mount reaction |F| = {react[0]:.3e} {units['force']}, |M| = {react[1]:.3e} "
                       f"{units['moment']} (expect ~0, report only).")
    tables = [
        Table("Displacement vs alpha dT (r - r_mount)", ["Metric", "Value", "Limit", "Result"],
              [["max |u - u_th| / max |u_th|", _e(err["t_err"]), _e(th.thermal_disp_rel),
                "ok" if err["t_err"] <= th.thermal_disp_rel else "FAIL"],
               ["max von Mises / (E alpha dT)", _e(s_ratio), _e(th.thermal_stress_rel),
                "ok" if (stresses and s_ratio <= th.thermal_stress_rel) else "FAIL"]]),
        Table("Top 20 stressed elements (what they touch)",
              ["Elem", "Type", "Stress", "Ratio", "Touches"], hot_rows),
        Table("Grids with the largest displacement error", ["Grid", "Relative error"],
              [[n, _e(v)] for n, v in err["worst_t"]]),
    ]
    if tot:
        tables.insert(1, Table("SPCFORCE resultant TOTALS (about the basic origin)", DOF_NAMES,
                               [[_e(v, "{:.6E}") for v in tot]],
                               "Expect ~0: a kinematic mount can't resist free expansion."))
    if eps_rows:
        tables.append(Table("EPSILON (UIM 5293)", ["Seq", "Epsilon", "External work", "Result"], eps_rows))
    ex = f06.excerpts
    excerpts = [(t, _join(ex[k])) for t, k in (("SPCFORCE resultant", "spcforce"),
                                               ("EPSILON", "epsilon")) if ex.get(k)]
    dist = np.linalg.norm(field.xyz - rm, axis=1)
    errs = np.linalg.norm(field.t - t_exp, axis=1) / (err["t_scale"] or 1.0)
    data = dict(t_err=err["t_err"], stress_ratio=s_ratio, sig_scale=sig_scale,
                dist=dist.tolist(), err=errs.tolist(), top=hot_rows)
    return CheckResult(key, title, status, summary, details, tables, data, excerpts)


# ============================================================ mass properties
def mass_from_block(b: MassBlock) -> dict:
    """mass, CG (x, y, z) and inertia about the CG [Ixx, Iyy, Izz, Ixy, Ixz, Iyz] from a
    WEIGHTCHECK/GPWG block. CG components come from the two mass-axis rows where each
    is defined (e.g. x from the Y and Z rows). I(S) products are +int(xy dm)."""
    X, Y, Z = b.cg
    cg = [(Y[0] + Z[0]) / 2, (X[1] + Z[1]) / 2, (X[2] + Y[2]) / 2]
    I = b.IS
    inertia = [I[0][0], I[1][1], I[2][2], I[0][1], I[0][2], I[1][2]]
    return dict(mass=b.mass[0], cg=cg, inertia=inertia)


def target_scale(units_name: str, mass_unit: str) -> float:
    """Factor converting a target mass (and inertia) in ``mass_unit`` to model mass units."""
    if mass_unit == "model":
        return 1.0
    if mass_unit == "lbm" and units_name.startswith("in"):
        return 1.0 / 386.0886     # lbm -> lbf-s^2/in
    if mass_unit == "kg" and units_name == "mm-t-s":
        return 1.0e-3             # kg -> tonne
    if mass_unit == "kg" and units_name == "m-kg-s":
        return 1.0
    raise ValueError(f"Target mass unit {mass_unit!r} doesn't apply to unit system {units_name!r}")


def check_mass_props(block: Optional[MassBlock], targets, units_name: str, units: dict,
                     excerpt: Optional[list] = None) -> CheckResult:
    key, title = "mass_props", "Mass, CG and inertia vs targets"
    if not targets.any_supplied():
        return CheckResult(key, title, "SKIPPED", "No CAD/target values entered.")
    if block is None or not block.mass or not block.IS:
        return error_result(key, title, "No WEIGHTCHECK/GPWG mass properties found in the free-free .f06.")
    fe = mass_from_block(block)
    try:
        k = target_scale(units_name, targets.mass_unit)
    except ValueError as exc:
        return error_result(key, title, str(exc))
    L = units["length"]
    rows, fails = [], []

    def pct(fe_v, tgt):
        return 100.0 * (fe_v - tgt) / abs(tgt) if tgt else float("inf")

    if targets.mass is not None:
        t = targets.mass * k
        d = pct(fe["mass"], t)
        ok = abs(d) <= targets.tol_mass_pct
        rows.append(["Mass", _e(fe["mass"], "{:.6g}"), _e(t, "{:.6g}"), f"{d:+.3f} %",
                     f"{targets.tol_mass_pct:g} %", "ok" if ok else "FAIL"])
        if not ok:
            fails.append(f"mass {d:+.2f}%")
    for i, ax in enumerate("XYZ"):
        t = targets.cg[i]
        if t is None:
            continue
        d = fe["cg"][i] - t
        ok = abs(d) <= targets.tol_cg_length
        rows.append([f"CG {ax}", _e(fe["cg"][i], "{:.5g}"), _e(t, "{:.5g}"), f"{d:+.4g} {L}",
                     f"{targets.tol_cg_length:g} {L}", "ok" if ok else "FAIL"])
        if not ok:
            fails.append(f"CG {ax} off by {d:+.3g} {L}")
    names = ["Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz"]
    for i, nm in enumerate(names):
        t = targets.inertia[i]
        if t is None:
            continue
        t = t * k
        if i >= 3 and targets.product_convention == "tensor":
            t = -t  # tensor products are -int(xy dm); I(S) prints +int(xy dm)
        fe_v = fe["inertia"][i]
        if i >= 3:
            # Products can be ~0; judge them against the largest moment of inertia instead of themselves.
            ref = max(abs(x) for x in fe["inertia"][:3])
            d = 100.0 * (fe_v - t) / ref if ref else float("inf")
            note = "% of max Iii"
        else:
            d = pct(fe_v, t)
            note = "%"
        ok = abs(d) <= targets.tol_inertia_pct
        rows.append([nm, _e(fe_v, "{:.5g}"), _e(t, "{:.5g}"), f"{d:+.3f} {note}",
                     f"{targets.tol_inertia_pct:g} %", "ok" if ok else "FAIL"])
        if not ok:
            fails.append(f"{nm} {d:+.2f}{'%' if i < 3 else '% of max I'}")
    status = "FAIL" if fails else "PASS"
    summary = "; ".join(fails) if fails else f"{len(rows)} supplied value(s) within tolerance"
    details = [
        "FE values: free-free WEIGHTCHECK G-set (MASS units), inertia I(S) about the CG in basic.",
        "I(S) prints products as +int(xy dm) (CAD convention), derived from the sample .f06; "
        f"targets were entered as '{targets.product_convention}' and "
        + ("compared directly." if targets.product_convention == "cad" else "sign-flipped before comparing."),
        f"Target mass unit: {targets.mass_unit} (scale to model units {k:g}).",
        "Product tolerances are a % of the largest principal-axis moment, since products are often ~0.",
    ]
    return CheckResult(key, title, status, summary, details,
                       [Table("FE vs target", ["Quantity", "FE", "Target", "Difference", "Tolerance", "Result"], rows,
                              "Shaded: each FE value next to the target it is compared with.",
                              [(i, 1, 2, "pair") for i in range(len(rows))])],
                       dict(fe=fe, rows=rows),
                       [("WEIGHTCHECK G-set (source of the FE values)", excerpt)] if excerpt else [])


# ============================================================== model summary
def check_model_summary(info, mount, base: int, gravity_spc_label: str,
                        real_spc_label: str) -> CheckResult:
    key, title = "model_summary", "Model summary"
    rows = [[k, v] for k, v in info.element_counts.items()]
    q_rows = [[name, s["count"], f"{s['min']:.4g}", f"{s['mean']:.4g}", f"{s['p95']:.4g}", f"{s['max']:.4g}",
               ", ".join(f"{e}" for e, _t, _v in s["worst"][:5])] for name, s in info.quality.items()]
    details = [
        f"{info.n_grids} grids, {info.n_elements} elements, {info.n_rigid} rigid elements, "
        f"{info.n_masses} mass elements.",
        f"Bounding box {np.round(info.bbox_min, 4).tolist()} to {np.round(info.bbox_max, 4).tolist()}, "
        f"diagonal L = {info.size:.4g}.",
        f"Mount grid {mount.grid} ({'auto-picked nearest the CG' if mount.auto else 'user-entered'}) at "
        f"{np.round(mount.xyz, 4).tolist()}, {mount.distance_to_cg:.4g} from the pyNastran CG "
        f"({100 * mount.distance_fraction:.1f}% of L).",
        f"Gravity mount: {gravity_spc_label}. Constrained modes: {real_spc_label}.",
        "Traceability: title '" + info.trace.get("title", "-") + "', subtitle '"
        + info.trace.get("subtitle", "-") + "', FEMAP model " + info.trace.get("femap_model", "-")
        + " (exported " + info.trace.get("femap_date", "-") + ").",
        "NASTRAN statements carried into every check deck: "
        + (", ".join(info.trace.get("nastran_statements", [])) or "none") + ".",
        f"Check-deck IDs reserved from {base} (no collision with model set IDs).",
        "Case control carried into every check deck: "
        + (", ".join(info.carried_case_control) if info.carried_case_control else "none") + ".",
        "Model PARAMs: " + ", ".join(f"{k}={v}" for k, v in info.params.items()) + ".",
    ] + info.notes
    tables = [Table("Element counts", ["Type", "Count"], rows)]
    if info.geomcheck:
        tables.append(Table("Elements beyond Simcenter's default GEOMCHECK limits (qrg ~8150; INFO, "
                            "Nastran itself only prints messages for these)",
                            ["Nastran test", "Types", "Limit", "Checked", "Beyond", "Worst elements"],
                            info.geomcheck))
    if q_rows:
        tables.append(Table("Element quality statistics (pyNastran; shells and solids; angles in degrees)",
                            ["Metric", "N", "Min", "Mean", "95th pct", "Max", "Worst elements"], q_rows))
    return CheckResult(key, title, "INFO", f"{info.n_grids} grids, {info.n_elements} elements",
                       details, tables, dict(element_counts=info.element_counts,
                                             quality={k: {kk: vv for kk, vv in v.items() if kk != "values"}
                                                      for k, v in info.quality.items()}))
