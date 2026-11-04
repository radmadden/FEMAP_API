"""The report's Method appendix: for each check, what was run, the pass criteria and the
reasoning behind them, with the governing references.

The text is generated from the run's own settings (thresholds, units, mount grid, SPC
sets, mode counts), so the appendix always states the criteria that were actually
applied. The design decisions cited as D1-D8 are documented in docs/design.md; the
reasoning is repeated here so the report stands on its own.

References:
  femci ~line   NASA GSFC FEMCI "The Book", Validity Checks (reference/FEMCI-The-Book.pdf)
  qrg ~line     Simcenter Nastran Quick Reference Guide 2025 (reference/QRG.pdf)
"""
from __future__ import annotations


def _g(x: float) -> str:
    return f"{x:.0e}" if x < 0.01 else f"{x:g}"


def method_sections(s, unit: dict, mount_grid: int, mount_auto: bool, gravity_label: str,
                    real_label: str, alpha: float) -> list[tuple[str, list[str]]]:
    """[(heading, [paragraphs])] for the checks selected in settings ``s``.

    unit: the UNIT_SYSTEMS entry (g, length/force/moment names); gravity_label and
    real_label describe the SPC sets used, as printed elsewhere in the report."""
    th = s.thresholds
    ft, mt = s.force_tol(), s.moment_tol()
    fu, mu, lu = unit["force"], unit["moment"], unit["length"]
    out: list[tuple[str, list[str]]] = []

    out.append(("General", [
        "Source of the checks: NASA GSFC FEMCI \"The Book\", section Validity Checks "
        "(femci ~1880), which defines four mathematical validity checks: unit enforced "
        "displacement and rotation, free-free dynamics with a stiffness equilibrium check, "
        "unit gravity loading, and unit temperature increase. The first three apply to every "
        "model used for statics or dynamics; the thermal check applies to models used for "
        "thermal distortion analysis (femci ~1891). Constrained modes and the mass-property "
        "comparison are added checks.",
        "Each check is a separate Simcenter Nastran run of the model's own bulk data, written "
        "unchanged except for the parameters each check sets (AUTOSPC, GRDPNT, POST). "
        "Non-structural mass, MPC and DMIG selections are carried over from the model's case "
        "control, so every run has the model's full mass and constraint equations.",
        f"Kinematic mount: grid {mount_grid} "
        f"({'chosen automatically as the qualifying grid nearest the CG' if mount_auto else 'specified for this run'}), "
        "constrained in all six DOF with SPC1. A qualifying grid has rotational stiffness (it "
        "lies on a shell, beam, bar or bush, or is an RBE2 independent grid), is not dependent "
        "on a rigid element or MPC, and has no permanent constraint. A grid on solid elements "
        "only would leave the rotation checks untested. FEMCI asks for the mount near the CG "
        "(femci ~1966); the report gives its distance from the CG.",
        f"Units: {s.units}; g = {unit['g']:g} {lu}/s^2. FEMCI's limits of 0.1 lb force and "
        f"1.0 in-lb moment (femci ~1965) are applied as {ft:.4g} {fu} and {mt:.4g} {mu}.",
        "Verdicts: PASS when every criterion is met; FAIL when any is not; INFO for report-only "
        "checks; ERROR when Nastran did not finish the run (a solver problem, not a model "
        "verdict); SKIPPED when nothing was requested. The run is also judged on EPSILON "
        f"(UIM 5293): a value above {_g(th.epsilon_max)}, which Nastran itself flags, fails "
        "the static checks (D7).",
    ]))

    if s.checks.get("freefree"):
        out.append(("Free-free modes and GROUNDCHECK", [
            f"Run: SOL 103 with no constraints, the first {s.n_modes_freefree} modes "
            f"(EIGRL), PARAM,AUTOSPC,{'YES' if s.autospc_freefree else 'NO'}. GROUNDCHECK on "
            f"sets G, N{', N+AUTOSPC' if s.autospc_freefree else ''}, F and A with grid "
            f"{mount_grid} as reference (qrg ~18934); WEIGHTCHECK on the same sets in mass units "
            "(qrg ~31138).",
            "Purpose: an unconstrained model must move as a rigid body. It must have exactly six "
            "zero-energy modes, and its stiffness matrix must not resist rigid-body motion. "
            "Grounding comes from springs to ground, rigid elements or MPCs that don't allow "
            "rigid motion, wrong SPCs, or AUTOSPC (femci ~2082-2127).",
            f"Criterion 1: max(f1..f6) / f7 <= {_g(th.rbm_ratio)}. The six rigid-body "
            "frequencies must sit at least four decades below the first elastic mode. FEMCI's "
            "absolute limit of 1e-4 Hz (femci ~2095) is not used: it does not scale with model "
            "size or units, and solid models with sound stiffness routinely exceed it from "
            "round-off. A seventh near-zero mode (a mechanism) makes f7 ~ 0 and fails the ratio "
            "(D1).",
            "Criterion 2: every GROUNDCHECK set prints PASS in all six directions. Nastran "
            "moves the stiffness matrix of each DOF set rigidly and compares the strain energy "
            "with its own limit, the largest stiffness term / 1e10. The first failing set "
            "localizes the cause: G for springs and DMIG, N for MPC and RBE3, F for SPCs, A for "
            "AUTOSPC or a reference grid far from the CG.",
            "WEIGHTCHECK (INFO): the change in rigid-body mass from the G-set to each reduced "
            "set; a nonzero change means MPCs or rigid elements gain or lose mass.",
        ]))

    if s.checks.get("constrained_modes"):
        out.append(("Constrained modes", [
            f"Run: SOL 103 on {real_label}, the first {s.n_modes_constrained} modes, with modal "
            "effective mass (MEFFMASS, qrg ~22311).",
            "Report only (D5): frequencies and effective-mass fractions per mode and cumulative. "
            "No frequency floor is applied, because mechanisms such as gimbals legitimately have "
            "near-zero bearing modes depending on whether the bearing rotations are locked.",
        ]))

    if s.checks.get("unit_disp"):
        out.append(("Unit enforced displacement and rotation", [
            f"Run: SOL 101, six subcases. Grid {mount_grid} is constrained 123456 and given an "
            "enforced displacement (SPCD, qrg ~119845) of 1.0 in one DOF per subcase: T1, T2 and "
            "T3 in length units, and R1, R2 and R3 in radians (femci ~1953).",
            "Purpose: driven from one grid, a correctly built model moves as a rigid body with "
            "no reaction and no internal load. Anything that grounds the model or resists rigid "
            "motion shows up as a reaction, a distorted displacement field or element forces.",
            f"Criterion 1, reaction: the SPCFORCE resultant moved to the mount grid, "
            f"|F| <= {ft:.4g} {fu} and |M| <= {mt:.4g} {mu} (femci ~1965).",
            f"Criterion 2, displacement field: every grid's displacement, transformed to the "
            "basic system, against the rigid-body field. A translation subcase expects "
            "u = e_k everywhere; a rotation subcase expects u = e_k x (r - r_mount) and "
            "rotation = e_k, which is exact in linear statics. Pass when "
            f"max|u - u_rigid| / max|u_rigid| <= {_g(th.ud_disp_rel)}. Rotations are checked only "
            "on grids with rotational stiffness.",
            f"Criterion 3, element forces: peak element force <= {ft:.4g} {fu} and moment "
            f"<= {mt:.4g} {mu}, per element type, at element centroids (shell values per unit "
            "length). CQUAD4 CORNER (bilinear-extrapolated) values are listed but not judged: "
            "under a 1 rad rigid rotation they reach tens of force units per length while the "
            "same elements' centroid values are ~1e-7, a recovery artifact rather than grounding "
            "(D6).",
        ]))

    if s.checks.get("gravity"):
        out.append(("Unit gravity", [
            f"Run: SOL 101, three subcases with 1 g (GRAV, g = {unit['g']:g} {lu}/s^2) in X, Y "
            f"and Z, on {gravity_label} (femci ~2231). WEIGHTCHECK gives the model mass in mass "
            "units.",
            "Purpose: under gravity the applied load (OLOAD) is the model weight, and the "
            "reactions (SPCFORCE) must balance it exactly. Load reaching ground anywhere else, "
            "for example through a grounded spring or an AUTOSPC-constrained DOF that carries "
            "load, appears as an imbalance. A mass, NSM or WTMASS error appears as an OLOAD that "
            "differs from mass x g. A statically determinate mount and the real mount give the "
            "same test; the kinematic mount is the default (D2).",
            f"Criteria, per subcase, each <= {_g(th.gravity_rel)}: force balance "
            "|F_OLOAD + F_SPC| / |F_OLOAD|; moment balance |M_OLOAD + M_SPC| / "
            "(|M_OLOAD| + |F_OLOAD| L), with L the model's bounding-box diagonal; weight "
            "| |F_OLOAD| - m g | / (m g). Vector norms are used because off-axis components are "
            "round-off, which a per-component percentage test would wrongly fail.",
        ]))

    if s.checks.get("thermal"):
        out.append(("Thermal soak", [
            f"Run: SOL 101 on the kinematic mount (grid {mount_grid}, femci ~2289, ~2297). Every "
            f"material is given the same CTE (alpha = {alpha:g}) with TREF = 0 (femci ~2319); "
            "temperature-dependent material entries (MATT) are removed. Every rigid element is "
            "given the same ALPHA, and the run uses RIGID = LAGRAN, under which rigid elements "
            "expand (qrg ~26012, D4). TEMPERATURE(INITIAL) = 0 and TEMPERATURE(LOAD) = 1, so "
            "dT = 1 and strain = alpha (T_load - T_init) (qrg ~29940).",
            "Purpose: with one CTE everywhere and a mount that cannot resist expansion, the exact "
            "solution is stress-free for any mix of elements, and every grid moves "
            "u = alpha dT (r - r_mount). Any stress or deviation points to a modeling defect: "
            "springs or bushes between non-coincident grids, anisotropic materials, or rigid "
            "elements that cannot expand. This theory field replaces the Book's THDEF program "
            "(femci ~2285).",
            f"Criteria (D8): max|u - u_theory| / max|u_theory| <= {_g(th.thermal_disp_rel)}; "
            f"peak element stress / (E_max alpha dT) <= {_g(th.thermal_stress_rel)}. "
            "E alpha dT is the fully restrained thermal stress. The 20 most stressed elements "
            "are listed with the springs, bushes and rigid elements they touch. The mount "
            "reaction is reported only.",
            "Scope: the check verifies model consistency, not material response, because every "
            "CTE is overwritten. A PASS assumes production thermal runs also use RIGID = LAGRAN "
            "with ALPHA on the rigid elements.",
        ]))

    t = s.targets
    if s.checks.get("mass_props") and t.any_supplied():
        out.append(("Mass properties", [
            "FE values come from the free-free WEIGHTCHECK G-set in mass units: mass, CG, and "
            "the inertia matrix I(S) about the CG in the basic system.",
            f"Tolerances: mass {t.tol_mass_pct:g} %, CG {t.tol_cg_length:g} {lu}, inertia "
            f"{t.tol_inertia_pct:g} %. Products of inertia are judged as a percentage of the "
            "largest moment of inertia, because they are often near zero.",
            "Sign convention: I(S) prints products of inertia as +integral(xy dm), the common "
            "CAD convention. This follows from the rigid-body mass matrix, which holds the "
            "tensor form -integral(xy dm). Targets entered in tensor convention are "
            f"sign-flipped before comparison (this run: '{t.product_convention}'). Targets in "
            f"'{t.mass_unit}' units are converted to model mass units.",
        ]))

    out.append(("References", [
        "NASA Goddard Space Flight Center, FEMCI \"The Book\", Validity Checks.",
        "Siemens, Simcenter Nastran Quick Reference Guide, 2025: GROUNDCHECK, WEIGHTCHECK, "
        "MEFFMASS, SPCD, TEMPD, TEMPERATURE, RIGID, RBE2/RBE3 ALPHA.",
        "fem_checks design description (fem_checks/docs/design.md), design decisions D1-D8, "
        "and background (fem_checks/docs/background.md).",
    ]))
    return out
