"""Run settings for the FEM validation checks.

Everything the user supplies (GUI form or JSON file) lives in one ``Settings``
dataclass. The same JSON drives a command-line run, so a GUI run can be repeated
exactly with ``python -m fem_checks <folder>/fem_checks.json``.

Unit systems
------------
The FEMCI Book thresholds are in in-lb (0.1 lb force, 1.0 in-lb moment, femci ~1965)
and its gravity is 386.4 in/s^2 with PARAM,WTMASS,0.00259 (femci ~2272). The user
picks a unit system; g and the force/moment thresholds follow from it so the same
physical limits apply in any units.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# 0.1 lbf and 1.0 in-lbf (the Book's limits) converted once here.
_LBF_TO_N = 4.4482216152605
_INLBF_TO_NMM = 112.98482933  # 1 in-lbf = 0.11298 N-m = 112.98 N-mm

UNIT_SYSTEMS: dict[str, dict] = {
    # Model in inch / lbf / second with mass already in lbf-s^2/in (no WTMASS).
    "in-lbf-s": dict(g=386.0886, length="in", force="lbf", moment="in-lbf",
                     force_tol=0.1, moment_tol=1.0, needs_wtmass=False),
    # Model with weight density (lbm) and PARAM,WTMASS,0.00259 converting to mass.
    "in-lbm (WTMASS)": dict(g=386.0886, length="in", force="lbf", moment="in-lbf",
                            force_tol=0.1, moment_tol=1.0, needs_wtmass=True),
    "mm-t-s": dict(g=9806.65, length="mm", force="N", moment="N-mm",
                   force_tol=0.1 * _LBF_TO_N, moment_tol=1.0 * _INLBF_TO_NMM,
                   needs_wtmass=False),
    "m-kg-s": dict(g=9.80665, length="m", force="N", moment="N-m",
                   force_tol=0.1 * _LBF_TO_N, moment_tol=1.0 * _INLBF_TO_NMM / 1000.0,
                   needs_wtmass=False),
}

CHECK_NAMES = ["model_summary", "freefree", "constrained_modes", "unit_disp",
               "gravity", "thermal", "mass_props"]


@dataclass
class MassTargets:
    """Optional CAD / target mass properties. ``None`` means "not supplied, skip".

    Inertia is about the CG in the FEM basic frame. ``product_convention`` says how
    the CAD tool reports products of inertia:
      "cad"    -> Ixy = +integral(x*y dm)  (SolidWorks, NX, Creo style)
      "tensor" -> Ixy = -integral(x*y dm)  (inertia tensor style)
    Nastran's GPWG I(S) prints the "cad" form (derived from the sample .f06, see
    research-fem-checks.md), so "tensor" targets get their products sign-flipped.
    """
    mass: Optional[float] = None
    cg: list = field(default_factory=lambda: [None, None, None])
    inertia: list = field(default_factory=lambda: [None] * 6)  # Ixx Iyy Izz Ixy Ixz Iyz
    tol_mass_pct: float = 1.0
    tol_cg_length: float = 0.05
    tol_inertia_pct: float = 5.0
    product_convention: str = "cad"
    # Units the targets are typed in: "model" (same as the deck's mass units),
    # "lbm" (in-lb systems; divided by 386.0886) or "kg" (SI systems).
    mass_unit: str = "model"

    def any_supplied(self) -> bool:
        return (self.mass is not None or any(v is not None for v in self.cg)
                or any(v is not None for v in self.inertia))


@dataclass
class Thresholds:
    """Pass/fail limits. Defaults are the decisions locked in plan-fem-checks.md."""
    rbm_ratio: float = 1e-4          # max rigid-body freq / first elastic freq (FEMCI's
                                     # absolute 1e-4 Hz is not used: user decision, plan #1)
    gravity_rel: float = 1e-4        # OLOAD vs SPCFORCE, and OLOAD vs mass*g
    ud_disp_rel: float = 1e-3        # unit-disp field vs rigid-body motion
    thermal_disp_rel: float = 1e-3   # thermal field vs alpha*dT*(r - r_mount)
    thermal_stress_rel: float = 1e-3  # max von Mises / (E_max*alpha*dT)
    epsilon_max: float = 1e-3        # Nastran flags EPSILON above this itself
    force_tol: Optional[float] = None   # None -> from the unit system
    moment_tol: Optional[float] = None  # None -> from the unit system


@dataclass
class Settings:
    deck: str = ""                    # FEMAP-exported .dat/.bdf (full model)
    nastran_exe: str = ""             # e.g. C:/Program Files/Siemens/FEMAP 2512/NASTRAN/bin/nastran.exe
    output_dir: str = ""              # "" -> <deck folder>/<deck stem>_fem_checks
    units: str = "in-lbf-s"
    mount_grid: int = 0               # 0 -> auto-pick the qualifying grid nearest the CG
    # The model's REAL boundary-condition SPC set (an SPC/SPC1/SPCADD set ID in the deck).
    # Required for the constrained-modes check; optional for gravity (see gravity_mount).
    real_spc_set: int = 0
    gravity_mount: str = "kinematic"  # "kinematic" (single-grid 123456 mount) or "real"
    checks: dict = field(default_factory=lambda: {
        "model_summary": True, "freefree": True, "constrained_modes": True,
        "unit_disp": True, "gravity": True, "thermal": False, "mass_props": True})
    n_modes_freefree: int = 10
    n_modes_constrained: int = 10
    autospc_freefree: bool = True
    alpha: float = 1.0e-5             # uniform CTE for the thermal soak (any value works)
    nastran_args: list = field(default_factory=lambda: ["scr=yes", "old=no", "batch=no"])
    run_timeout_s: Optional[float] = None
    targets: MassTargets = field(default_factory=MassTargets)
    thresholds: Thresholds = field(default_factory=Thresholds)

    # ------------------------------------------------------------------ helpers
    def unit(self) -> dict:
        return UNIT_SYSTEMS[self.units]

    def force_tol(self) -> float:
        t = self.thresholds.force_tol
        return t if t is not None else self.unit()["force_tol"]

    def moment_tol(self) -> float:
        t = self.thresholds.moment_tol
        return t if t is not None else self.unit()["moment_tol"]

    def resolved_output_dir(self) -> Path:
        if self.output_dir:
            return Path(self.output_dir)
        deck = Path(self.deck)
        return deck.parent / f"{deck.stem}_fem_checks"

    def validate(self, need_nastran: bool = True) -> list[str]:
        """Plain-English problems that stop a run. Empty list = OK."""
        problems = []
        if not self.deck:
            problems.append("No model deck selected.")
        elif not Path(self.deck).is_file():
            problems.append(f"Model deck not found: {self.deck}")
        if need_nastran:
            if not self.nastran_exe:
                problems.append("No Nastran executable selected.")
            elif not Path(self.nastran_exe).is_file():
                problems.append(f"Nastran executable not found: {self.nastran_exe}")
        if self.units not in UNIT_SYSTEMS:
            problems.append(f"Unknown unit system {self.units!r}; pick one of {list(UNIT_SYSTEMS)}.")
        if self.n_modes_freefree < 7:
            problems.append("Free-free mode count must be at least 7 (6 rigid + 1 elastic) "
                            "for the rigid-body frequency ratio check.")
        if self.n_modes_constrained < 1:
            problems.append("Constrained mode count must be at least 1.")
        if self.checks.get("constrained_modes") and not self.real_spc_set:
            problems.append("Constrained modes needs the model's real constraint SPC set ID.")
        if self.gravity_mount not in ("kinematic", "real"):
            problems.append("Gravity mount must be 'kinematic' or 'real'.")
        if self.checks.get("gravity") and self.gravity_mount == "real" and not self.real_spc_set:
            problems.append("Gravity on the real mount needs the real constraint SPC set ID.")
        if self.alpha <= 0:
            problems.append("Thermal CTE (alpha) must be positive.")
        if self.targets.mass_unit not in ("model", "lbm", "kg"):
            problems.append("Target mass unit must be 'model', 'lbm' or 'kg'.")
        if self.targets.mass_unit == "lbm" and not self.units.startswith("in"):
            problems.append("Target mass unit 'lbm' only applies to the inch unit systems.")
        if self.targets.mass_unit == "kg" and self.units.startswith("in"):
            problems.append("Target mass unit 'kg' only applies to the SI unit systems.")
        if self.targets.product_convention not in ("cad", "tensor"):
            problems.append("Product-of-inertia convention must be 'cad' or 'tensor'.")
        unknown = set(self.checks) - set(CHECK_NAMES)
        if unknown:
            problems.append(f"Unknown checks: {sorted(unknown)}")
        return problems

    # ------------------------------------------------------------------ JSON
    def to_json(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        d = dict(d)
        # Older settings files: gravity_spc_set (one field for both uses) -> new fields.
        old = d.pop("gravity_spc_set", 0)
        if old and not d.get("real_spc_set"):
            d["real_spc_set"], d["gravity_mount"] = old, "real"
        targets = MassTargets(**d.pop("targets", {}))
        th = dict(d.pop("thresholds", {}))
        th.pop("rbm_abs_hz", None)
        thresholds = Thresholds(**th)
        s = cls(**d)
        s.targets, s.thresholds = targets, thresholds
        # Fill any checks missing from an older JSON with the defaults.
        defaults = cls().checks
        s.checks = {**defaults, **s.checks}
        return s

    @classmethod
    def from_json(cls, path: Path) -> "Settings":
        return cls.from_dict(json.loads(Path(path).read_text()))
