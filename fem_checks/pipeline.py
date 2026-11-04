"""The whole run, start to finish. The GUI and the command line both call run().

Steps (plan-fem-checks.md, Algorithm):
  1 settings -> 2 read model + summary -> 3 mount grid -> 4 write bulk data
  -> 5 write check decks -> 6 run Nastran -> 7 parse .f06/.op2 -> 8 evaluate
  -> 9 PDF report + results.json
"""
from __future__ import annotations

import datetime as _dt
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import checks as C
from . import decks as D
from . import model as M
from . import report as RP
from . import results as R
from .f06 import parse_f06
from .runner import RunResult, run_nastran
from .settings import Settings


@dataclass
class RunSummary:
    output_dir: Path
    report: Optional[Path]
    results_json: Optional[Path]
    checks: list = field(default_factory=list)
    runs: list = field(default_factory=list)
    error: Optional[str] = None

    @property
    def overall(self) -> str:
        st = [c.status for c in self.checks]
        if self.error or "ERROR" in st:
            return "ERROR"
        return "FAIL" if "FAIL" in st else "PASS"


def _existing_run(deck: Path) -> RunResult:
    """--no-run mode: pick up outputs already sitting next to the deck."""
    def find(suf):
        for p in deck.parent.iterdir():
            if p.name.lower() == (deck.stem + suf).lower():
                return str(p)
        return None
    f06, op2 = find(".f06"), find(".op2")
    return RunResult(str(deck), f06, op2, None, 0.0, None if f06 else "no existing .f06")


def run(settings: Settings, log: Callable[[str], None] = print,
        run_solver: bool = True) -> RunSummary:
    out = settings.resolved_output_dir()
    summary = RunSummary(out, None, None)
    problems = settings.validate(need_nastran=run_solver)
    if problems:
        summary.error = " ".join(problems)
        log("Cannot start: " + summary.error)
        return summary
    out.mkdir(parents=True, exist_ok=True)
    logfile = (out / "fem_checks.log").open("a")

    def say(msg: str):
        log(msg)
        logfile.write(f"{_dt.datetime.now():%H:%M:%S} {msg}\n")
        logfile.flush()

    try:
        return _run(settings, out, summary, say, run_solver)
    except Exception as exc:  # report the reason in plain words, keep the traceback in the log
        summary.error = f"{type(exc).__name__}: {exc}"
        say("STOPPED: " + summary.error)
        logfile.write(traceback.format_exc())
        return summary
    finally:
        logfile.close()


def _run(s: Settings, out: Path, summary: RunSummary, say, run_solver: bool) -> RunSummary:
    unit = s.unit()
    s.to_json(out / "fem_checks.json")
    say(f"Output folder: {out}")

    # STEP 2: read the model -------------------------------------------------
    say(f"Reading model {s.deck} ...")
    model = M.read_model(Path(s.deck), out / "work")
    info = M.model_info(model, Path(s.deck), quality=s.checks.get("model_summary", True), log=say)
    say(f"  {info.n_grids} grids, {info.n_elements} elements, pyNastran mass {info.mass:.6g}")
    if unit["needs_wtmass"] and "WTMASS" not in info.params:
        say("  WARNING: unit system expects PARAM,WTMASS but the deck has none.")
        info.notes.append("Unit system 'in-lbm (WTMASS)' selected but the deck has no PARAM,WTMASS.")

    # STEP 3: mount grid ------------------------------------------------------
    mount = M.choose_mount(model, info, s.mount_grid)
    say(f"Mount grid {mount.grid} ({'auto' if mount.auto else 'user'}), "
        f"{mount.distance_to_cg:.4g} from CG ({100 * mount.distance_fraction:.1f}% of model size)")
    base = M.reserved_base(model)
    ids = D.DeckIds(base)
    kin_label = f"kinematic mount (grid {mount.grid}, 123456)"
    real_label = f"model SPC set {s.real_spc_set}"
    if s.real_spc_set and not M.spc_set_exists(model, s.real_spc_set):
        raise ValueError(f"Real constraint SPC set {s.real_spc_set} is not in the model deck "
                         f"(sets found: {sorted(set(model.spcs) | set(model.spcadds))}).")
    if s.gravity_mount == "real":
        gravity_spc, spc_label = s.real_spc_set, real_label
    else:
        gravity_spc, spc_label = ids.mount_spc, kin_label

    # STEP 4: bulk data -------------------------------------------------------
    which = [k for k in D.DECK_NAMES if s.checks.get(k)]
    if s.checks.get("mass_props") and s.targets.any_supplied() and "freefree" not in which:
        which.insert(0, "freefree")  # mass properties come from the free-free WEIGHTCHECK
        say("Free-free run added: the mass-properties check reads its WEIGHTCHECK output.")
    (out / "model_bulk.bdf").write_text(M.bulk_text(model))
    say("Wrote model_bulk.bdf (large-field, model PARAMs AUTOSPC/GRDPNT/POST removed)")
    lagran_bad = [eid for eid in model.rigid_elements if eid > M.LAGRAN_MAX_ID]
    thermal_log = []
    if "thermal" in which:
        tmodel = M.read_model(Path(s.deck), out / "work")
        thermal_log = M.make_thermal_uniform(tmodel, s.alpha)
        (out / "model_bulk_thermal.bdf").write_text(M.bulk_text(tmodel))
        for line in thermal_log:
            say("  thermal: " + line)

    # STEP 5: decks ---------------------------------------------------------------
    paths = D.write_decks(out, which, ids, mount.grid, gravity_spc, s.real_spc_set, unit["g"],
                          s.n_modes_freefree, s.n_modes_constrained, s.autospc_freefree,
                          info.carried_case_control, info.trace)
    say("Wrote decks: " + ", ".join(p.name for p in paths.values()))

    # STEP 6: run Nastran -------------------------------------------------------
    runs: dict[str, RunResult] = {}
    for key, p in paths.items():
        if run_solver:
            say(f"Nastran: {p.name}")
            r = run_nastran(s.nastran_exe, p, s.nastran_args, s.run_timeout_s, say)
            say(f"  {'done' if r.ok else 'FAILED: ' + r.error} in {r.seconds:.0f} s")
        else:
            r = _existing_run(p)
        runs[key] = r
    summary.runs = list(runs.values())

    # STEP 7: parse -------------------------------------------------------------
    f06s = {}
    for key, r in runs.items():
        f06s[key] = parse_f06(r.f06) if r.f06 else None
    messages = [(Path(r.deck).name, m) for k, r in runs.items() if f06s.get(k)
                for m in f06s[k].messages]
    versions = sorted({f.version for f in f06s.values() if f})

    # STEP 8: evaluate ------------------------------------------------------------
    th = s.thresholds
    results = []
    if s.checks.get("model_summary"):
        results.append(C.check_model_summary(info, mount, base, spc_label, real_label))
    if s.checks.get("freefree"):
        results.append(C.check_freefree(f06s.get("freefree"), th, s.autospc_freefree))
    if s.checks.get("constrained_modes"):
        results.append(C.check_constrained(f06s.get("constrained_modes"), real_label))
    if s.checks.get("unit_disp"):
        results.append(_unit_disp(runs.get("unit_disp"), f06s.get("unit_disp"), model, mount, s, unit, say))
    if s.checks.get("gravity"):
        f = f06s.get("gravity")
        mass = None
        if f:
            g_blocks = [b for b in f.mass_blocks if b.mass]
            mass = g_blocks[0].mass[0] if g_blocks else None
        results.append(C.check_gravity(f, mass, unit["g"], info.size, th, unit, spc_label))
    if s.checks.get("thermal"):
        results.append(_thermal(runs.get("thermal"), f06s.get("thermal"), model, mount, s, info,
                                unit, lagran_bad, thermal_log, say))
    if s.checks.get("mass_props"):
        f = f06s.get("freefree")
        block = None
        if f:
            gset = [b for b in f.mass_blocks if b.source == "WEIGHTCHECK" and b.dof_set == "G" and b.IS]
            block = gset[0] if gset else next((b for b in f.mass_blocks if b.IS), None)
        results.append(C.check_mass_props(block, s.targets, s.units, unit))
    summary.checks = results

    # STEP 9: report ------------------------------------------------------------
    charts_dir = out / "charts"
    charts_dir.mkdir(exist_ok=True)
    charts = {}
    for c in results:
        try:
            if c.key == "freefree" and c.data.get("freq_hz"):
                charts[c.key] = RP.chart_freefree(c.data, charts_dir / "freefree.png", th.rbm_ratio)
            elif c.key == "constrained_modes" and c.data.get("freq_hz"):
                charts[c.key] = RP.chart_constrained(c.data, charts_dir / "constrained.png")
            elif c.key == "thermal" and c.data.get("dist"):
                charts[c.key] = RP.chart_thermal(c.data, charts_dir / "thermal.png", th.thermal_disp_rel)
            elif c.key == "model_summary" and info.quality:
                p = RP.chart_quality(info.quality, charts_dir / "quality.png")
                if p:
                    charts[c.key] = p
        except Exception as exc:
            say(f"  chart for {c.key} skipped: {exc}")
    overall = "ERROR" if any(c.status == "ERROR" for c in results) else (
        "FAIL" if any(c.status == "FAIL" for c in results) else "PASS")
    header = {
        "Overall": overall,
        "Model deck": s.deck,
        "Date": f"{_dt.datetime.now():%Y-%m-%d %H:%M}",
        "Nastran version(s)": ", ".join(versions) or "-",
        "Units": f"{s.units} (g = {unit['g']:g})",
        "Mount grid": f"{mount.grid} ({'auto' if mount.auto else 'user'}), "
                      f"{mount.distance_to_cg:.4g} from CG",
        "Model title / subtitle": f"{info.trace.get('title', '-')} / {info.trace.get('subtitle', '-')}",
        "FEMAP model": info.trace.get("femap_model", "-"),
        "Gravity mount": spc_label,
        "Constrained-modes SPC": real_label if s.real_spc_set else "-",
        "Output folder": str(out),
    }
    summary.results_json = out / "results.json"
    RP.write_json(summary.results_json, dict(header=header, settings=s, model=_model_json(info),
                                             mount=mount, runs=summary.runs, checks=results))
    pdf = out / "fem_checks_report.pdf"
    try:
        summary.report = RP.build_pdf(pdf, header, results, charts, messages, summary.runs)
    except PermissionError:
        # Windows locks a PDF that is open in a viewer (the user's first full run stopped
        # here). Write a time-stamped copy instead of losing the whole run.
        alt = out / f"fem_checks_report_{_dt.datetime.now():%Y%m%d_%H%M%S}.pdf"
        say(f"  {pdf.name} is open in another program (locked); writing {alt.name} instead")
        summary.report = RP.build_pdf(alt, header, results, charts, messages, summary.runs)
    say(f"Overall: {overall}. Report: {summary.report}")
    return summary


def _model_json(info: M.ModelInfo) -> dict:
    d = {k: v for k, v in info.__dict__.items() if k != "quality"}
    d["quality"] = {k: {kk: vv for kk, vv in v.items() if kk != "values"} for k, v in info.quality.items()}
    return d


def _unit_disp(run, f06, model, mount, s, unit, say):
    key, title = "unit_disp", "Unit enforced displacement / rotation"
    bad = C._fatal_or_missing(f06, key, title)
    if bad:
        return bad
    if run is None or not run.op2:
        return C.error_result(key, title, "No OP2 for the unit-displacement run.")
    say("Reading unit-displacement OP2 ...")
    op2 = R.load_op2(run.op2)
    fields = R.displacements_basic(op2, model, {})
    peaks = {sc: R.element_force_peaks(op2, sc) for sc in range(1, 7)}
    return C.check_unit_disp(f06, fields, peaks, mount.xyz, set(mount.rotation_grids),
                             s.thresholds, s.force_tol(), s.moment_tol(), unit)


def _thermal(run, f06, model, mount, s, info, unit, lagran_bad, thermal_log, say):
    key, title = "thermal", "Thermal soak (+1 deg, uniform CTE, kinematic mount)"
    if lagran_bad:
        return C.check_thermal(None, None, [], mount.xyz, s.alpha, info.e_max, s.thresholds,
                               {}, model, unit, lagran_bad)
    bad = C._fatal_or_missing(f06, key, title)
    if bad:
        return bad
    if run is None or not run.op2:
        return C.error_result(key, title, "No OP2 for the thermal run.")
    say("Reading thermal OP2 ...")
    op2 = R.load_op2(run.op2)
    fields = R.displacements_basic(op2, model, {})
    stresses = R.element_stress_peaks(op2, 1)
    res = C.check_thermal(f06, fields.get(1), stresses, mount.xyz, s.alpha, info.e_max,
                          s.thresholds, R.connector_neighbors(model), model, unit, [])
    res.details += ["Thermal deck changes: " + "; ".join(thermal_log)]
    return res
