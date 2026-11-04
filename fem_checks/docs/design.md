# FEM Validation Checks: Design Description

Tool: `fem_checks/` (Python package). Background and findings: `background.md` (this folder;
cited below as "background §n"). Executable walkthrough: `fem_checks_design.ipynb`. References:
`reference/FEMCI-The-Book.pdf` (`femci ~line`) and `reference/QRG.pdf` (`qrg ~line`). Reference
model and outputs: `fem_checks/reference_model/`.

## 1. Scope

- Solver: Simcenter Nastran (SOL 101 and SOL 103).
- Input: a full model deck exported from FEMAP. FEMAP is not otherwise used.
- Checks:
  - free-free normal modes with GROUNDCHECK and WEIGHTCHECK;
  - constrained normal modes (report only);
  - unit enforced displacement and rotation (6 subcases);
  - unit gravity X/Y/Z with OLOAD vs SPCFORCE comparison;
  - +1° uniform thermal soak (for thermal distortion models);
  - model summary (counts, element quality);
  - optional mass, CG and inertia against CAD or target values.
- Output: one folder holding the check decks, all solver output, a PDF report and
  `results.json`.
- Operation: a tkinter GUI or a command line driven by the same settings file; Nastran is
  run automatically.

## 2. Design decisions

| ID | Decision | Rationale |
|---|---|---|
| D1 | **Free-free criterion:** PASS when max(f1…f6) / f7 ≤ 1e-4 and every GROUNDCHECK set prints PASS. FEMCI's absolute 1e-4 Hz limit (femci ~2095) is not used. | The absolute limit does not scale with model size or units; the reference model fails it on 4 of 6 rigid-body modes while GROUNDCHECK passes by 4-7 orders of magnitude. Six decades between the last rigid-body mode and the first elastic mode demonstrates the absence of grounding; a mechanism makes f7 ≈ 0 and fails the ratio. |
| D2 | **Gravity mount:** the kinematic mount by default, or the model's real SPC set when selected. | Any statically determinate or real mount must give SPCFORCE = −OLOAD; a mismatch indicates load reaching ground elsewhere. The kinematic mount matches established practice on the reference model. |
| D3 | **Separate decks per check** (five Nastran runs). | Each check needs a different constraint set; separate decks keep each run's SPC selection simple and isolate the thermal material overwrite from the other checks. |
| D4 | **Rigid elements expand in the thermal soak:** every RBAR/RBE1/RBE2/RBE3/RROD gets `ALPHA` = α and the deck runs `RIGID=LAGRAN` (qrg ~26012). | Matches the production thermal-distortion setup. Under the default `RIGID=LINEAR` rigid elements carry no thermal load, and every RBE2 spider would appear as a defect. A PASS assumes production thermal runs also use LAGRAN with ALPHA. |
| D5 | **Constrained modes are report only (INFO),** with no frequency floor. | Gimbals legitimately have one or two near-zero bearing modes depending on whether the bearing rotations are locked; a floor would fail correct models. |
| D6 | **Unit-displacement criteria,** all per subcase: (a) mount reaction ≤ 0.1 lbf / 1.0 in-lbf, converted to model units (femci ~1965); (b) displacement field equal to rigid-body motion within 1e-3; (c) element forces at element centroids within the same limits. CQUAD4 CORNER values above the limit are listed as INFO. | (a)+(b) detect any grounding. On the reference model, centroid forces are ≤ 1.6e-7 while CORNER (bilinear) values reach 33-82 lbf/in under a 1 rad rotation: a recovery artifact. GPFORCE is not requested (very large output; covered by (a)+(b)). |
| D7 | **EPSILON:** FAIL when Nastran flags it (> 1e-3); otherwise reported. | Nastran's own threshold for ill-conditioning. |
| D8 | **Thermal criteria** (background §6): displacement error ≤ 1e-3 relative to α·ΔT·(r − r_m); max von Mises / (E_max·α·ΔT) ≤ 1e-3. | Uniform α on a kinematic mount is exactly stress-free; relative criteria are unit-free. Replaces the Book's THDEF program. |

Other fixed choices:
- GROUNDCHECK passes only if every requested set (G, N, N+AUTOSPC, F, A) prints PASS.
- Gravity: OLOAD vs SPCFORCE and OLOAD vs m·g within 1e-4, using vector norms (background
  §5.2).
- PARAM,AUTOSPC in the free-free run is selectable (default YES, the QRG default). NO is
  stricter but can fail on singular DOF; the Book notes that AUTOSPC can cause a spurious
  A-set failure (femci ~2127).
- The constrained-modes run requires the model's real constraint SPC set; the tool checks
  that the set exists in the deck.
- Free-free mode count 10 by default (at least 7, for D1).

## 3. Run inputs

GUI fields, stored in `<output>/fem_checks.json`:

| Field | Default | Notes |
|---|---|---|
| Model deck (.dat/.bdf from FEMAP) | none | Full deck; its executive and case control are replaced |
| Nastran executable | last used | `nastran.exe` (the `nastranw.exe` launcher returns before the job finishes; the tool substitutes `nastran.exe`) |
| Output folder | `<deck>_fem_checks\` | All decks, output and the report |
| Unit system | in-lbf-s | in-lbf-s (g 386.0886), in-lbm with WTMASS, mm-t-s (9806.65), m-kg-s (9.80665) |
| Mount grid | auto (nearest CG) | Or a grid ID |
| Real constraint SPC set | none | Required for constrained modes; optional mount for gravity (D2) |
| Gravity mount | kinematic | kinematic or real |
| Checks | all except thermal | Thermal only for thermal-distortion models (femci ~1891) |
| Mode counts | 10 / 10 | Free-free / constrained |
| AUTOSPC in free-free | YES | See §2 |
| Mass-property targets | blank (skipped) | Mass, CG x/y/z, Ixx…Iyz about the CG in basic; tolerances; product convention (+∫xy CAD or −∫xy tensor); units (model, lbm, kg) |
| Thresholds | as §2 | Editable on the Advanced tab |

## 4. Algorithm

1. **Settings.** Load from the GUI or JSON and validate: paths, unit system, mode counts,
   and that the real SPC set is given when constrained modes are selected.
2. **Read the model** with `BDF(mode='nx')`, after stripping the FEMAP `ENDDATA` checksum.
   Collect card and element counts, element quality (background §5.4), pyNastran mass
   properties, E_max, and traceability data: TITLE, SUBTITLE, FEMAP model path and export
   date, and the leading `NASTRAN`/`INIT` statements.
3. **Choose the mount grid.** A grid qualifies when it has rotational stiffness (shell,
   beam, bar, bush, or RBE2 independent grid), is not dependent on a rigid element or MPC,
   has no permanent constraint (GRID PS field). SPC cards in the model's own
   sets do not disqualify a grid, because the check decks select only their own set. Auto
   mode takes the qualifying grid nearest the CG; an entered grid is validated with the same
   rules.
4. **Write the bulk data** as `<model>_bulk.bdf` in pyNastran's bulk-only (punch) mode,
   large-field format. PARAM AUTOSPC, GRDPNT, POST, OGEOM and PRGPST are removed; every
   other PARAM (K6ROT, WTMASS, …) is kept. A reserved ID block (9,000,000+, checked against
   the model) is used for added cards. For the thermal check, a fresh read of the deck is
   modified and written as `<model>_bulk_thermal.bdf`:
   - every MAT1/2/3/8/9/11 gets α (shear terms 0) and TREF 0;
   - PCOMP/PCOMPG TREF 0;
   - MATT* entries are removed;
   - every rigid element gets ALPHA = α (D4).
5. **Write the check decks** `01_<model>_freefree.dat` … `05_<model>_thermal.dat`. Each
   holds:
   - a `$` provenance header (source deck, FEMAP model, export date, title);
   - the carried NASTRAN statements;
   - the model's TITLE and SUBTITLE, with the check name in LABEL;
   - the carried case control (NSM, MPC, DMIG selections);
   - the check's own case control and bulk entries, and an `INCLUDE` of the bulk data.

   | Deck | SOL | SPC | Case control / bulk |
   |---|---|---|---|
   | 01 freefree | 103 | none | `GROUNDCHECK(PRINT,SET=(G,N,N+AUTOSPC,F,A),GRID=m,DATAREC=YES)=YES`, `WEIGHTCHECK(...,CGI=YES,MASS)=YES`, EIGRL ND=N, `PARAM,GRDPNT,0`, AUTOSPC per setting |
   | 02 constrained_modes | 103 | real set | `MEFFMASS(PLOT,PRINT,GRID=0,PARTFAC,MEFFM,FRACSUM,SUMMARY)=YES`, EIGRL ND=N |
   | 03 unit_disp | 101 | `SPC1,<id>,123456,<mount>` | 6 subcases, `LOAD` → `SPCD` 1.0 in DOF 1-6; DISP, SPCF, OLOAD, `FORCE(PLOT,CORNER)` |
   | 04 gravity | 101 | kinematic or real (D2) | `GRAV` X/Y/Z at g; `WEIGHTCHECK(SET=(G),MASS)`; DISP, SPCF, OLOAD |
   | 05 thermal | 101 | `SPC1,<id>,123456,<mount>` | `RIGID=LAGRAN`, `TEMPERATURE(INITIAL)` = TEMPD 0.0, `TEMPERATURE(LOAD)` = TEMPD 1.0; DISP, SPCF, STRESS |

   In `SPC1,<id>,123456,<mount>`, `<id>` is the reserved SPC set ID selected in case
   control; the constrained grid is always the chosen mount grid.
6. **Run Nastran** on each deck: `nastran.exe <deck> scr=yes old=no batch=no`, in the output
   folder, waiting for completion. Old outputs of the same deck are deleted first. No .f06,
   or a FATAL message, makes that check ERROR; Nastran's console output is logged.
7. **Parse.** From the .f06: GROUNDCHECK, WEIGHTCHECK and GPWG blocks, eigenvalues, MEFFMASS
   fractions, OLOAD and SPCFORCE RESULTANT totals, EPSILON, AUTOSPC count, FATAL/WARNING
   messages, plus verbatim excerpts of each table for the report. From the OP2:
   displacements (transformed from each grid's CD to basic), element forces and stresses.
8. **Evaluate** each check to PASS / FAIL / INFO / ERROR / SKIPPED per §2.
9. **Report.** Write the PDF and `results.json`. If the PDF is locked (open in a viewer), a
   time-stamped copy is written instead.

## 5. Output

Folder `<deck>_fem_checks\`:
- `fem_checks.json`: the settings used.
- `<model>_bulk.bdf`, `<model>_bulk_thermal.bdf`.
- `01_<model>_freefree.dat` … `05_<model>_thermal.dat`, each with `.f06/.op2/.f04/.log`.
- `fem_checks_report.pdf` (landscape):
  - page 1: model, traceability, units, mount grid and CG offset, the PASS/FAIL table;
  - one section per check:
    - verdict and criteria;
    - full data tables: all eigenvalue columns, GROUNDCHECK per set with limits, OLOAD vs
      SPCFORCE per component and subcase, mount displacement per subcase, element force
      peaks;
    - chart;
    - the verbatim .f06 blocks the verdict was read from.

    Values compared by a criterion share the same shading.
  - Appendix A, Method: for each check, what was run, the pass criteria with the values
    applied in that run, and the reasoning (D1-D8) with references, so the report stands
    on its own;
  - Appendix B: run times and every FATAL/WARNING message.
- `results.json`: every number in the report.
- `fem_checks.log`: the run log, with full error traces.

## 6. Software structure

| Module | Function |
|---|---|
| `__main__.py` | Command line: `python -m fem_checks [settings.json] [--no-run]`; no argument opens the GUI |
| `gui.py`, `gui_paths.py` | tkinter form, run on a worker thread, log pane; remembered settings |
| `settings.py` | Settings dataclass ↔ JSON; unit systems; validation |
| `model.py` | Deck reading, summary, quality, traceability, mount selection, bulk writing, thermal overwrite |
| `decks.py` | Check deck writing, file naming, reserved IDs |
| `runner.py` | Nastran execution and output collection |
| `f06.py` | .f06 table parsers and verbatim excerpts |
| `results.py` | OP2 reading, coordinate transformation, theoretical fields, force/stress peaks |
| `checks.py` | PASS/FAIL evaluation and report tables |
| `report.py` | PDF (reportlab), charts (matplotlib), `results.json` |
| `method.py` | Method appendix text, generated from the run settings |
| `setup_env.py` + `setup_fem_checks.bat` | Workstation setup: Python 3.12 virtual environment, packages, Jupyter kernel, VS Code configuration, Nastran discovery, self-test |
| `tests/` | Parser tests on the reference .f06 files; model, deck and check tests on a generated model and the reference deck |

## 7. Assumptions and limitations

- Simcenter Nastran only; parsers verified on Simcenter 2306 and 2512 output.
- The deck must be readable by pyNastran 1.4.1; RTRPLT is not supported.
- The bulk data is rewritten by pyNastran. On the reference deck all 41,538 cards are
  field-identical after the round trip.
- The enforced displacements act along the mount grid's output (CD) axes; the theory fields
  use those axes, so a mount with a cylindrical or spherical CD is valid.
- Mass-property targets are about the CG in the FEM basic frame.
- The auto-pick uses pyNastran's CG, which excludes NSM; the report's GPWG values are
  authoritative.
- The thermal check overwrites every CTE: it verifies model consistency, not material
  response.
- Shell element forces are per unit length (D6).
- Python 3.10-3.12 (background §4).

## 8. Verification status

Automated tests (`python -m pytest fem_checks/tests`, 20 tests) cover:
- the .f06 parsers against the reference .f06 files;
- the check logic on those files (free-free PASS at ratio 3.2e-6; gravity PASS and FAIL
  with a 1% mass error; mass properties in both product conventions);
- deck writing, mount rules and bulk-writing regressions;
- a lossless bulk round trip of the reference deck.

Verified on the reference deck with the reference OP2 files:
- unit-displacement field equal to rigid-body motion within 4e-8 in all six subcases
  (24,851 grids; 7,302 checked in rotation);
- mount reactions ≤ 2.8e-4;
- gravity OLOAD/SPCFORCE balance within 1e-10.

Live runs on a workstation completed the free-free, constrained-modes, unit-displacement and
gravity checks and produced the report. The thermal check has not yet been run live.

## 9. Setup and operation

Setup (once per workstation):
1. Install Python 3.12 (python.org; select *py launcher* and *tcl/tk and IDLE*).
2. Run `setup_fem_checks.bat` in the repository root. It creates `.venv`, installs
   `fem_checks/requirements.txt` and `requirements-dev.txt`, registers the Jupyter kernel
   "fem_checks (.venv)", merges VS Code settings and launch configurations, locates
   `nastran.exe`, and runs the tests.

Operation:
```
.venv\Scripts\python -m fem_checks                                 # GUI
.venv\Scripts\python -m fem_checks <out>\fem_checks.json           # repeat a run
.venv\Scripts\python -m fem_checks <out>\fem_checks.json --no-run  # re-evaluate outputs
```

## 10. Verification checklist (per new model or installation)

1. Setup ends with "Setup complete."; the GUI opens.
2. The GPWG/WEIGHTCHECK mass, CG and I(S) in `01_<model>_freefree.f06` match an independent
   free-free run of the model to every printed digit (confirms the bulk rewrite).
3. The mount grid is sensible, and the report's CG offset agrees with the GPWG CG.
4. Free-free eigenvalues and GROUNDCHECK sets match an independent run.
5. Unit-displacement: rotation subcases show rigid-body translations (spot-check two grids:
   u = θ × (r − r_m)).
6. Gravity: OLOAD in the load direction equals mass × g.
7. Thermal: one distant grid moves α·(r − r_m); review the top-20 stressed elements in FEMAP.
   They should point at spring or bush connections or anisotropic materials, not at RBE2
   spiders. If RBE2 spiders dominate, confirm `RIGID = LAGRAN` in the .f06 echo.
8. Seed a defect (e.g. a CELAS2 to ground): the GROUNDCHECK G-set must FAIL, and the report
   must show it.

## 11. Revision history

| Revision | Change |
|---|---|
| A | Initial implementation. |
| B | Report shows full data tables and verbatim .f06 excerpts with matched-value shading; check files named after the model; check decks carry title, subtitle and FEMAP provenance. |
| C | Corrections from live runs: bulk data written in pyNastran bulk-only mode (pyNastran asserts on case-control lines longer than ~68 characters); `nastranw.exe` replaced by `nastran.exe`; time-stamped report when the PDF is locked; mount rule refers to permanent constraints only. |
| D | Repository reorganized: package documents in `fem_checks/docs/`, reference model and outputs in `fem_checks/reference_model/`, reference PDFs in `reference/`; README files added. |
| E | Method appendix added to the report (criteria, values applied and reasoning for each check). |
| F | Displacements rotated from each grid's output system to basic by grid ID (any R/C/S system); the mount grid may have any output system; every check's verdict and reason, and Nastran FATAL messages, written to the run log. |
