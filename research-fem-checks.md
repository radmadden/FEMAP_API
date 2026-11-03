# Research: FEM validation checks (FEMCI Book), push-button Python tool

**Status:** research done; round-2 answers recorded 2026-11-03. Waiting on the sample
Simcenter files and the Quick Reference Guide (due 2026-11-04) before writing
`plan-fem-checks.md`.

Renamed from `research-femap-fem-checks.md`: per Q0, this is now a Python tool, not a FEMAP
macro. FEMAP's only job is exporting the model's .dat/.bdf.

## Goal

A push-button tool for **Simcenter Nastran** models:
1. The user points it at a model deck exported from FEMAP (.dat/.bdf) and at their local
   Nastran executable.
2. It writes the check decks, runs Nastran, parses the OP2 and .f06 files, and writes one
   report with PASS/FAIL per check and the data behind each verdict.

The checks:
1. Free-free modes, first 10, plus `GROUNDCHECK` (FEMCI "Free-Free Dynamics with a
   Stiffness Equilibrium Check").
2. **Constrained modes** on the real mount (added at the user's request, not in FEMCI).
3. Unit enforced displacement and rotation, T1-T3 and R1-R3 (FEMCI).
4. Unit gravity in X, Y and Z, with an OLOAD vs SPCFORCE resultant comparison and a
   weight check (FEMCI).
5. Unit temperature increase (thermal soak) on a kinematic mount (FEMCI).
6. Model summary: node and element counts, element quality, and an optional mass, CG
   and inertia check against CAD or target values.

## Decisions locked from round 1 (user `>>` answers, 2026-11-03)

- **Q0, architecture:** Python end to end. FEMAP only exports the deck. ("ok lets roll with
  that")
- **Q1, gravity:** "unit grab" was the 1g gravity check. ±g and combined cases: not asked
  for; X, Y and Z only.
- **Q1a, thermal mount:** use the same grid as the unit-displacement setup (FEMCI requires
  a kinematic mount, ~2289/~2297).
- **Q2, pass/fail:**
  - GROUNDCHECK passes only if every set (G/N/F/A) reports PASS.
  - OLOAD vs SPCFORCE should agree to about **1e-4 relative**.
  - Thermal criteria: Claude to propose (see "Thermal soak pass criteria" below).
- **Q3, mount grid:** offer both: the user enters a grid ID, or the tool picks the grid
  nearest the CG.
- **Q4, gravity mount:** ask the user for the real SPC set ID.
- **Q5, report:** must show PASS/FAIL per check plus the data behind it. Format: Claude to
  recommend (below).
- **Q6, outputs:** keep everything in an output folder.
- **Q7, running:** fully automatic. The user points to a local Nastran install, gives a
  general .dat/.bdf, and gets a simple GUI. "As push-button as possible."
- **Q8, solver:** Simcenter Nastran (NX) only.
- **Q9, extras:** element quality report, node and element counts.
- **Units** (inline note): ask for the unit system (in-lb-s, slinch, SI-mm, SI-m) so the
  tool sets g, WTMASS handling and force thresholds to match.
- **Gravity pass criterion** (inline note): judge it from the **OLOAD RESULTANT** and
  **SPCFORCE RESULTANT** tables.

## What FEMCI "The Book" says (confirmed in FEMCI-The-Book.pdf)

Line refs are into `pdftotext -layout FEMCI-The-Book.pdf femci.txt` (`femci ~line`).
Section "Validity Checks" starts at femci ~1880. The Book lists **four** mathematical
checks: the ground check is part of the free-free check, and the reaction comparison is
part of the gravity check. The first three apply to every statics/dynamics model; the
thermal check is for models used in thermal distortion analysis (femci ~1891).

| Check | Constraint (per Book) | Pass criteria (per Book) |
|---|---|---|
| Unit enforced disp/rotation, SOL 101, 6 subcases (femci ~1953) | `SPC1 123456` on **one grid**, `SPCD` 1.0 on that grid in each DOF. That grid has no other SPCs and should be near the CG (~1966) | Translation subcases: 1.0 in the input DOF, 0.0 in the other 5. Rotation subcases: 1.0 rad about the input axis, other two rotations 0 (translations nonzero, rigid-body). Element forces and grid-point force balance ≈ 0, max **0.1 lb / 1.0 in-lb** (~1965) |
| Free-free modes + GROUNDCHECK, SOL 103 (femci ~2082) | Remove all external SPCs (the `SPC=100` note covers only normal/drilling plate rotations) | ≥ 6 rigid-body modes at **≤ 1.0E-04 Hz** (~2095). `GROUNDCHECK(SET=ALL)=YES` gives G/N/F/A-set strain energy with PASS/FAIL against a limit. A G-set error propagates to later sets; `PARAM,AUTOSPC,YES` can falsely fail the A-set (~2127) |
| Unit gravity X/Y/Z, SOL 101, 3 subcases (femci ~2231) | **Real mount** | SPC forces only at mount points. They sum to the total weight in the load direction, ~0 in the others. `PARAM,GRDPNT,0` (~2270) |
| Unit temperature increase, SOL 101 (femci ~2276) | **Kinematic mount**: "one GRID point constrained in all 6 DOFs" (~2289, ~2297) | Displacements = α·L·ΔT theory. Grid-point force balance zero, SPC forces only at the mount. `TEMPD` = Tref + 1 (~2315). **All MAT cards: same CTE, same Tref** (~2319). No RBE2s between non-coincident nodes (~2318) |

Other Book checks (femci ~1897-1950, "Computer-Aided" and "Common Sense" accuracy checks)
and whether Python can automate them without a pre/post-processor:

| Book check | Automatable from the deck? |
|---|---|
| Mass reasonable (~1915) | **Yes, in scope:** mass, CG and inertia vs optional CAD/target values (see "Mass-properties check") |
| Coincident nodes (~1903) | Possible, **dropped by user (round 2)** |
| Free edges (~1901) | Possible for shells, **dropped by user (round 2)** |
| Plate distortion (~1906) | **Yes, in scope:** element quality summary (`element_quality`, `delete_bad_elements.py:403`) |
| CELAS between non-coincident grids (~1934) | Possible, **dropped by user (round 2)** |
| RBE3 used as structure, big RBE2s (~1932) | Count and list only; this needs engineering judgment |
| Singularities / AUTOSPC (~1944) | **Yes**, from the .f06: the grid point singularity table, the AUTOSPC count, EPSILON and MAXRATIO messages, and a count of all FATAL/WARNING messages per run |
| Units, WTMASS (~1920) | Partly. The tool asks for the unit system and flags a missing `PARAM,WTMASS` when the units call for one (in-lb-s) |
| Bar orientation, plate normals, coincident elements | **No**, they need visualization. Leave them to FEMAP |

## Key findings: pyNastran (verified in pyNastran 1.4.1 source, installed with `pip install pyNastran`)

| Need | pyNastran API | Ref |
|---|---|---|
| Read the deck, Simcenter dialect | `BDF(mode='nx')`, `read_bdf(...)` | `bdf/bdf.py:526,542`, `:4862` |
| Mass, CG, inertia before any run (WTMASS-aware), used to auto-pick the mount grid | `mesh_utils.mass_properties.mass_properties(model, ...)` | `mass_properties.py:176` |
| Node/element/card counts | `model.get_bdf_stats()` | `bdf.py:3565` |
| Element quality (min/max interior angle, skew, aspect, taper, area ratio, min edge) | `element_quality(model)` | `delete_bad_elements.py:403` |
| Free edges (shells) | `free_edges(model)` | `free_edges.py:13` |
| Coincident nodes | `find_closest_nodes` / KD-tree | `find_closest_nodes.py:15` |
| Read OP2, Simcenter dialect | `OP2(mode='nx')` | `op2/op2.py` (`set_mode`) |
| OP2 results | `eigenvalues`, `eigenvectors`, `displacements`, `spc_forces`, `load_vectors` (OLOAD), `grid_point_forces`, `grid_point_weight`, `op2_results.force.*`, `op2_results.stress.*`, `op2_results.strain_energy` | attributes checked on an `OP2()` instance |

**Not in pyNastran, so we write our own .f06 parser for:**
- the GROUNDCHECK tables (UIM 7570),
- the OLOAD RESULTANT and SPCFORCE RESULTANT tables,
- the grid point singularity table and AUTOSPC count,
- EPSILON, MAXRATIO and the FATAL/WARNING scan.

These are fixed-format text blocks.

**Not verified yet: Simcenter-specific syntax.** This environment has no Simcenter Nastran
manuals, so these are [likely] but unconfirmed:
- `GROUNDCHECK(SET=ALL,GRID=n)=YES` syntax, and the .f06 wording.
- `SPCD` referenced via `LOAD=` (the static case needs the SPC set to include the SPCD
  DOFs).
- `MEFFMASS(ALL)=YES` for the constrained modes.
- The RBE2 `ALPHA` field.
- The AUTOSPC default in SOL 101/103.

The Book's samples are MSC output (~2148). Before trusting the parser we need one real
Simcenter .f06 (see round-2 Q1).

## Engineering notes

**Runs.** Four Nastran jobs, written into one output folder:

| Run | SOL | SPC | Contents |
|---|---|---|---|
| `01_freefree` | 103 | none | first 10 modes, `GROUNDCHECK(SET=ALL,GRID=mount)`, `PARAM,GRDPNT,0` (GPWG) |
| `02_constrained_modes` | 103 | real set | first 10 modes (or N), MEFFMASS |
| `03_statics` | 101 | per subcase | subcases 1-6: unit SPCD on the mount (SPC=mount set). Subcases 7-9: 1g X/Y/Z (SPC=real set). Output: DISP, SPCF, OLOAD, GPFORCE, ELFORCE, STRESS |
| `04_thermal` | 101 | mount set | **modified materials** (uniform CTE, uniform Tref), `TEMPD` = Tref + 1 |

Thermal stays a separate deck: the material overwrite must not touch the other checks'
input, even though in theory it wouldn't change them.

**Mount grid trap.** The auto-pick must only choose grids with **rotational stiffness**:
grids on shells, beams, springs or rigid elements. A grid that touches only solid elements
has no rotational DOF stiffness. Enforcing R1-R3 on it does nothing useful, so the
rotation subcases would "pass" meaninglessly. If the nearest qualifying grid is far from
the CG, say so in the report. GROUNDCHECK's A-set result gets sensitive to that distance
(femci ~2223).

**Deck handling.** Read the FEMAP deck with pyNastran. Drop its executive and case
control, and keep the bulk data untouched except for:
- adding the check cards: SPC1 and SPCD for the mount, GRAV, TEMPD, PARAMs;
- the material CTE/Tref overwrite in run 04.

Use a reserved ID range for added cards (e.g. 9_000_000+) and check that it doesn't
collide with existing IDs. If pyNastran chokes on a FEMAP card, fall back to text-level
splicing: keep the bulk data verbatim and write only case control plus an include.

**Thermal soak pass criteria (my proposal, answering your Q2).** With a uniform α, a
uniform ΔT and a kinematic mount, the exact solution is stress-free, with
u = α·ΔT·(r − r_m) for every grid. That's exact for any element mix, whatever E is.
So any stress is a modeling defect, and the test can be relative and unit-free:
- **Displacement:** max |u_FEM − u_theory| / max |u_theory| ≤ **1e-3**. This replaces the
  Book's THDEF program.
- **Stress:** max von Mises / (E_max·α·ΔT) ≤ **1e-3**. E·α·ΔT is the fully-restrained
  thermal stress, the natural scale.
- **Reactions:** SPC force at the mount ≈ 0. Report it only, no pass/fail.
- **Hotspots:** list the top 20 stressed elements with their types. The usual culprits are
  RBE2s without ALPHA, CBUSH/CELAS between non-coincident grids, and MAT8/MAT9 left
  anisotropic.

**Unit-dependent thresholds.** The Book's 0.1 lb and 1.0 in-lb become 0.445 N and
0.113 N·m in SI-m, or 0.445 N and 113 N·mm in SI-mm. g is 386.4 in/s², 9806.65 mm/s² or
9.80665 m/s². For in-lb-s the user must already have `PARAM,WTMASS,0.00259`; the tool
checks for it.

**Report: PDF (round-2 decision).** HTML had no charting advantage; charts come from
matplotlib either way. Its only edge was wide tables that don't paginate. PDF wins as an
archival, attachable record for a review package.
- Build it with **reportlab** (tables and page layout; one extra `pip install`). Charts come
  from **matplotlib**, already a pyNastran dependency (`requires('pyNastran')`), embedded
  as images.
- Layout:
  1. Page 1: model summary and the **PASS/FAIL table, one row per check**.
  2. One section per check with the tables behind the verdict.
  3. Appendix: long lists (top-N elements, quality tables).
- Charts: free-free and constrained frequencies, effective-mass fractions, element-quality
  histograms, thermal displacement error vs theory.
- Also write `results.json` with every number, so later runs can be diffed against it.
- Wide tables (e.g. 6-DOF unit-displacement results × 6 subcases) need landscape pages or
  splitting. That's a layout detail for the plan.

**GUI: recommendation for your Q7.** Use a **tkinter** window. It's in Python's standard
library, so nothing extra to install, which matters on a locked-down work PC. It has:
- file pickers for the deck and the `nastran.exe`, and an output folder;
- a units dropdown;
- the real SPC set ID;
- the mount grid (auto or entered);
- optional mass-properties targets: mass, CG (x, y, z) and inertia (Ixx, Iyy, Izz, Ixy,
  Ixz, Iyz about the CG), each with a tolerance. Blank fields are skipped;
- an AUTOSPC on/off checkbox for the free-free run;
- a Run button and a log pane.

It saves settings to `fem_checks.json` next to the deck, so a re-run is one click. The
same settings file drives a command-line run (`python -m fem_checks fem_checks.json`) for
scripting.

**Testing reality.** Nastran can't run here. I can unit-test deck building and the
OP2/.f06 parsers here, but only against real Simcenter outputs you provide. Without them
the parsers are written blind against MSC samples from the Book.

**Repo conventions.** CLAUDE.md's conventions are for `.bas` macros. For this tool I'd
put a package in `fem_checks/` (GUI, deck writer, runner, parsers, report) with tests
under `fem_checks/tests/`, and add a short Python section to CLAUDE.md when we implement.

**Mass-properties check (added round 2).** The user enters CAD or target values for mass,
CG and the inertia tensor, each optional, and the tool compares them to GPWG from run 01.
Traps to handle:
- **Product-of-inertia sign convention.** CAD tools usually report Ixy = +∫xy dm. Nastran's
  GPWG prints the inertia *tensor*, whose off-diagonal terms are −∫xy dm [likely; confirm
  on the sample .f06]. The GUI asks which convention the targets use, and the report
  states it.
- **Reference point and frame.** Targets are taken as inertia about the CG, in the basic
  coordinate system. If the CAD frame differs from the FEM basic frame, the user has to
  transform first. The tool doesn't attempt that.
- **Weight vs mass units.** GPWG output may be in weight units, scaled by 1/WTMASS
  [guessing; confirm on the sample .f06]. Comparisons must happen in one unit, and the
  report labels which.
- Tolerances: per-quantity % for mass and inertia; absolute length for CG, since a % of a
  near-zero CG coordinate is meaningless.

## Decisions locked from round 2 (user answers in chat, 2026-11-03)

- **Q1/Q2:** sample Simcenter .f06/.op2/deck and the Quick Reference Guide coming
  2026-11-04. The plan waits for them.
- **Q3:** thermal 1e-3 relative displacement and stress limits: OK for now.
- **Q4:** **no frequency floor** on constrained modes. Gimbals legitimately have one or two
  near-zero "mechanism" modes, depending on whether the bearing rotations are locked. The
  constrained-modes check is **report-only (INFO)**, with no PASS/FAIL.
- **Q5:** AUTOSPC in the free-free run is a user option.
- **Q6:** **PDF report**, not HTML (see Report above). GUI: tkinter, no objection raised.
- **Q7:** user can `pip install pyNastran` on the work PC.
- **Dropped:** coincident nodes, free edges and the CELAS-geometry check. GROUNDCHECK still
  catches a bad spring as a G-set FAIL, just without naming it.
- **Added:** optional mass, CG and inertia check against CAD/target values (above).

## Open design questions, round 2 (answered above)

1. **Sample files:** can you add one real Simcenter Nastran .f06 with GROUNDCHECK
   output, plus a small model deck exported from FEMAP (any model, even a plate)? Ideally
   also the .op2 from a SOL 101 run with OLOAD/SPCF requested. Without these, the .f06
   parser is guesswork against MSC-format samples.
2. **Simcenter manual:** do you have the Simcenter Nastran Quick Reference Guide (PDF)
   to drop in the repo like `api.pdf`? It would let me confirm the syntax listed under
   "Not verified yet".
3. **Thermal criteria:** OK with the relative 1e-3 displacement and stress limits above?
4. **Constrained modes pass/fail:** there's no Book criterion. Proposal: FAIL if any mode
   falls below a user-set floor (default **1 Hz**, which catches mechanisms and
   unconstrained parts). Report the first N frequencies and the effective-mass fractions.
   Also an optional "first mode ≥ requirement" input?
5. **AUTOSPC in the free-free run:** the Book says AUTOSPC can falsely fail the GROUNDCHECK
   A-set. Run free-free with `PARAM,AUTOSPC,NO`? That's more honest, but a model with
   unattached drilling DOFs may then hit singularities. Or leave AUTOSPC as the deck has
   it and report the A-set with that caveat?
6. **Report and GUI:** OK with HTML + `results.json`, and a tkinter GUI with a saved
   settings file?
7. **Python on your work PC:** which Python version, and can you `pip install pyNastran`
   (it pulls in numpy and scipy)? If pip is blocked, the plan changes.
