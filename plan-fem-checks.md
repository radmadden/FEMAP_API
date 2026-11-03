# Plan: push-button FEM validation checks (Simcenter Nastran, Python)

Research: `research-fem-checks.md`. Reference docs: `FEMCI-The-Book.pdf` (femci ~line),
`QRG.pdf` (qrg ~line).

## Decisions locked from the research review

- **Architecture:** Python end to end. FEMAP only exports the model deck. No FEMAP API.
- **Solver:** Simcenter Nastran only.
- **Checks:**
  - free-free modes + GROUNDCHECK;
  - constrained modes;
  - unit enforced displacement/rotation (6 subcases);
  - 1g gravity X/Y/Z with OLOAD vs SPCFORCE;
  - 1-degree thermal soak;
  - model summary (node/element counts, element quality);
  - optional mass/CG/inertia vs CAD targets.
- **Dropped:** coincident nodes, free edges, CELAS-geometry check.
- **Kinematic mount:** one grid, SPC1 123456. Either user-entered or auto-picked nearest the
  CG. The same grid serves unit-displacement, thermal and the GROUNDCHECK/WEIGHTCHECK
  reference.
- **Gravity mount:** ask for the SPC set ID.
- **Thermal soak:** kinematic mount, all CTEs and Trefs made uniform. PASS needs relative
  displacement error ≤ 1e-3 vs α·ΔT·(r − r_m) and max von Mises / (E·α·ΔT) ≤ 1e-3.
- **GROUNDCHECK:** PASS only if every requested set prints PASS.
- **OLOAD vs SPCFORCE:** agreement to 1e-4 relative.
- **Constrained modes:** report only (INFO), with no floor. Gimbals can have
  near-zero bearing modes.
- **AUTOSPC in the free-free run:** user option.
- **Units:** the user picks the unit system; g and the force thresholds follow from it.
- **Report:** PDF (reportlab + matplotlib charts) plus `results.json`. Everything goes in
  one output folder.
- **Running:** the tool runs the local Nastran executable automatically. Simple tkinter
  GUI, settings saved to JSON, same JSON usable from the command line.
- **Python:** user can `pip install pyNastran` (and reportlab) on the work PC.

## Decisions to confirm (all 8 approved by the user 2026-11-04; #8 = model deck, pending upload)

1. **Rigid-body mode criterion.** Your sample model's rigid-body modes are 1.0e-4 to
   4.3e-4 Hz. **It fails FEMCI's absolute ≤ 1e-4 Hz limit on 5 of 6 modes**, yet
   GROUNDCHECK passes everywhere by orders of magnitude.
   - Proposal: PASS if f_6 / f_7 ≤ 1e-4 (six decades between the last rigid mode and the
     first elastic mode). Your sample sits at 3.2e-6.
   - Print the Book's absolute 1e-4 Hz alongside as INFO.
   - The alternative is to keep the Book's limit and accept that good solid models fail.
2. **Gravity on which mount.** Your `1G_check_template.dat` runs gravity on the
   single-grid mount, not the real mount the Book asks for. Proposal: the GUI field
   "Gravity / constrained-modes SPC set" defaults to **the kinematic mount** when left
   blank, or uses the set ID you enter. Both are valid: any statically determinate or
   real mount must give SPCFORCE = −OLOAD, so a mismatch means grounding.
3. **Separate decks instead of one combined statics deck.** That's 5 Nastran runs:
   free-free, constrained modes, unit displacement, gravity, thermal. Your own templates
   are separate decks, and splitting keeps each run's SPC simple. It costs a few minutes
   of extra runtime, and nobody's watching it anyway.
4. **Rigid elements in the thermal soak.**
   - Default: leave RBE2s rigid. A non-expanding RBE2 spanning distance shows up as
     stress, which the Book treats as a defect (femci ~2318).
   - Option (checkbox): set RBE2 `ALPHA` = α and add `RIGID=LAGRAN` (qrg ~112944) so
     rigid elements expand too. Only stress from real defects remains.
5. **Unit-displacement pass criteria.** Three parts, all must pass:
   - (a) mount SPCFORCE resultant ≤ 0.1 lbf / 1.0 in-lbf, converted to the chosen units
     (femci ~1965);
   - (b) displacement field matches rigid-body motion, max |u − u_rb| / max |u_rb| ≤ 1e-3;
   - (c) max element force per type (CBUSH/CBEAM/CBAR/shells) ≤ the same 0.1 / 1.0
     thresholds. Shell forces are per unit length, so this is a judgment call. Top-N
     offenders are listed either way.

   GPFORCE is not requested. It's huge on a 25k-grid model and (a)+(b) cover it.
6. **Free-free mode count:** ND = 10 by default (6 rigid + 4 elastic), editable. The
   criterion in #1 needs at least 7.
7. **EPSILON:** FAIL if Nastran flags it (> 1e-3); report the value otherwise.
8. **Sample deck:** I still need the **full exported model deck** (`.dat` with bulk data)
   that produced the sample .op2s. The templates are fragments. Without the deck I can
   test the parsers but not deck writing, mount auto-pick or the rigid-body/thermal
   displacement theory checks.

## What the user supplies at run time

GUI fields, saved to `fem_checks.json` next to the deck:

| Field | Default | Notes |
|---|---|---|
| Model deck (.dat/.bdf from FEMAP) | none | Full deck; its exec/case control is ignored |
| Nastran executable | last used | e.g. `C:\Program Files\Siemens\Simcenter3D\NXNASTRAN\bin\nastran.exe` |
| Output folder | `<deck>_fem_checks\` | All decks, .f06, .op2, report go here |
| Unit system | in-lbf-s | in-lbf-s (g 386.1), in-lbm with WTMASS (g 386.1), mm-t-s (9806.65), m-kg-s (9.80665) |
| Mount grid | Auto (nearest CG) | Or enter a grid ID |
| Gravity / constrained-modes SPC set | blank = kinematic mount | Decision to confirm #2 |
| Checks to run | all except thermal | Thermal only for thermal-distortion models (femci ~1891) |
| Free-free / constrained mode count | 10 / 10 | |
| AUTOSPC in free-free | YES | QRG default; NO is stricter, but may fail on singularities |
| RBE2s expand in thermal | off | Decision to confirm #4 |
| Mass-property targets | blank (skip) | Mass, CG x/y/z, Ixx..Iyz about CG in basic, each with tolerance; product convention +∫xy (CAD) or −∫xy (tensor) |
| Thresholds | defaults above | Editable in an "Advanced" frame |

## Algorithm

1. **Load settings** from the GUI or the JSON. Validate the paths and that the Nastran exe
   exists.
2. **Read the model** with `BDF(mode='nx')` and `read_bdf(..., xref=True)`. The model
   summary comes from here:
   - node/element/property/material counts by card type;
   - element quality via `element_quality()`;
   - mass properties via `mass_properties()`.

   If pyNastran fails to read the deck, stop with the card name it choked on.
3. **Pick the mount grid.** If auto, take candidates that pass all of:
   - (a) a GRID connected to an element with rotational stiffness: shell, CBEAM, CBAR,
     CBUSH, or an RBE2 independent grid;
   - (b) **not** a dependent (m-set) DOF of any RBE2/RBE3/MPC. Nastran can't SPC an m-set
     DOF;
   - (c) no existing SPC in the model's sets.

   Choose the candidate nearest the pyNastran CG. For a user-entered grid, apply the same
   tests and refuse with a reason if one fails. Record the distance to the CG as a fraction
   of the model size.
4. **Write the bulk data once.** Write `model_bulk.bdf` with pyNastran in **16-character
   large-field format**, so coordinates and properties don't lose precision. Leave out the
   model's PARAM AUTOSPC/GRDPNT/POST/K6ROT and any EIGRL/EIGR, so each check deck controls
   its own. Added cards use a reserved ID block (9,000,001+), checked for collisions.
5. **Write the check decks.** Each is executive + case control + small bulk + `INCLUDE`:
   - `01_freefree.dat`: SOL 103, no SPC.
     - `GROUNDCHECK(PRINT,SET=(G,N,N+AUTOSPC,F,A),GRID=m,DATAREC=YES)=YES`
     - `WEIGHTCHECK(PRINT,SET=(G,N,N+AUTOSPC,F,A),GRID=m,CGI=YES,MASS)=YES`
     - `PARAM,GRDPNT,m` gives the GPWG about the mount; CG and I(S) are about the CG
       regardless.
     - EIGRL with ND = N, `DISP(PLOT)=ALL`, `PARAM,AUTOSPC` per option.
   - `02_constrained_modes.dat`: SOL 103, SPC = gravity set (or mount),
     `MEFFMASS(PRINT,PLOT,FRACSUM,SUMMARY)=YES`, EIGRL ND = N.
   - `03_unit_disp.dat`: SOL 101, `SPC1 9000001 123456 m`.
     - 6 subcases, `LOAD` → `SPCD` 1.0 in DOF 1..6.
     - Output: `DISP(PLOT)`, `SPCF(PLOT)`, `OLOAD(PLOT)`, `FORCE(PLOT,CORNER)`.
   - `04_gravity.dat`: SOL 101, SPC = gravity set.
     - 3 subcases with `GRAV` in X/Y/Z at g for the unit system.
     - Output: `DISP`, `SPCF`, `OLOAD`; `WEIGHTCHECK(SET=G,MASS)=YES`.
   - `05_thermal.dat`: SOL 101, mount SPC.
     - Bulk: `model_bulk_thermal.bdf`, the same bulk with every material overwritten:
       - MAT1 A=α, TREF=0;
       - MAT2 A1=A2=α, A3=0;
       - MAT8 A1=A2=α, TREF=0;
       - MAT9 A1=A2=A3=α, A4..A6=0;
       - PCOMP/PCOMPG TREF=0;
       - MATT* removed.
     - α = 1.0e-5 (arbitrary; all criteria are normalized).
     - Case control: `TEMP(INIT)=9000021` (TEMPD 0.0), `TEMP(LOAD)=9000022` (TEMPD 1.0).
     - Output: `DISP`, `SPCF`, `STRESS(PLOT)`; RBE2 option per decision #4.
6. **Run Nastran** on each deck in turn: `nastran.exe <deck> scr=yes old=no batch=no
   out=<folder>`. Wait for it to finish, then capture the exit code, .f06, .f04 and .log.
   A failed run (FATAL in the .f06, or no .op2) marks that check **ERROR** and the tool
   moves on. The GUI log shows progress.
7. **Parse:**
   - **From the .f06 (our parser):**
     - GROUNDCHECK tables;
     - WEIGHTCHECK blocks (mass, CG, I(S), % change per set);
     - eigenvalue table;
     - MEFFMASS fractions;
     - OLOAD/SPCFORCE RESULTANT TOTALS;
     - EPSILON;
     - AUTOSPC singularity count;
     - every FATAL/WARNING message.
   - **From the OP2 (pyNastran):** displacements, SPC forces, element forces, stresses,
     GPWG.
   - Displacements are transformed to the basic system using each grid's CD.
8. **Evaluate** each check to PASS / FAIL / INFO / ERROR:
   - **Free-free:**
     - ≥ 6 modes below the rigid/elastic split;
     - f6/f7 criterion (decision #1);
     - every GROUNDCHECK set PASS;
     - WEIGHTCHECK % change = 0 for every reduced set (INFO if nonzero, with the value).
   - **Constrained modes:** INFO. First N frequencies, effective-mass fractions per mode
     and cumulative.
   - **Unit displacement:** the three criteria in decision #5, per subcase.
   - **Gravity:**
     - per subcase, |F_OLOAD + F_SPC| / |F_OLOAD| ≤ 1e-4;
     - |M_OLOAD + M_SPC| / (|M_OLOAD| + |F_OLOAD|·L) ≤ 1e-4;
     - |F_OLOAD| vs mass·g ≤ 1e-4;
     - EPSILON not flagged.
   - **Thermal:**
     - displacement error ≤ 1e-3;
     - stress ratio ≤ 1e-3;
     - top-20 stressed elements listed with type and nearest RBE2/CBUSH;
     - mount reaction reported.
   - **Mass properties:** each supplied target within its tolerance. Mass and inertia use a
     % tolerance; CG uses a length. Values come from free-free GPWG I(S), which is
     +∫xy, CAD convention (see research). If the user picked the −∫xy convention, the
     tool flips the target's sign before comparing.
   - **Model summary:** INFO; quality histograms plus the worst 20 elements per metric.
9. **Write the report** (step 8 results → PDF) and `results.json`.

## Output

Folder `<deck>_fem_checks\`:
- `fem_checks.json`: settings used, so the run can be repeated.
- `model_bulk.bdf`, `model_bulk_thermal.bdf`.
- `01_freefree.dat` .. `05_thermal.dat`, each with its `.f06/.op2/.f04/.log`.
- `fem_checks_report.pdf`:
  - Page 1: model name, date, Nastran version, units, mount grid and its CG offset, and
    the **PASS/FAIL table** (one row per check, with key numbers).
  - One section per check: the tables behind the verdict, plus charts (mode frequencies
    log scale, effective mass, thermal error map vs distance, quality histograms).
  - Appendix: top-N lists, all FATAL/WARNING messages, deck listings of the case control
    used.
- `results.json`: every number in the report.

## Helper routines (package `fem_checks/`)

| Module | Job | Edge cases |
|---|---|---|
| `__main__.py` | CLI: `python -m fem_checks <settings.json>`; no args opens the GUI | |
| `gui.py` | tkinter form, Advanced frame, log pane, Run button (runs on a worker thread so the window doesn't freeze) | remembers the last Nastran path |
| `settings.py` | dataclass ↔ JSON, unit table (g, force/moment thresholds) | validation messages in plain English |
| `model.py` | read the deck, counts, quality, mass props, mount auto-pick, bulk writing, thermal material overwrite | m-set/SPC'd grid refusal; ID collisions; MATT removal logged |
| `decks.py` | write the 5 check decks from templates | reserved ID block |
| `runner.py` | subprocess call to Nastran, wait, collect files | timeouts, missing .op2, FATAL detection |
| `f06.py` | fixed-format parsers (one function per table) | multiple Simcenter versions; page breaks inside tables; "----" cells |
| `results.py` | OP2 reads, CD→basic transforms, rigid-body and thermal theory fields | grids with no output |
| `checks.py` | PASS/FAIL logic, normalized metrics | missing runs → ERROR, not FAIL |
| `report.py` | reportlab PDF + matplotlib charts, `results.json` | landscape pages for wide tables |
| `tests/` | parser tests against the supplied `.f06`s (expected numbers taken from them), deck-writing tests on a small generated model | |

## Assumptions / limits

- Simcenter Nastran only. MSC .f06 wording differs, and the parsers won't be tested on it.
- The model deck reads in pyNastran. Exotic or FEMAP-only cards may not; the tool then
  stops and names the card. Writing the bulk back out through pyNastran is the main
  fidelity risk. Mitigation: large-field output, plus first-run checklist item 2.
- Coordinate systems: theory fields are computed in basic coordinates. Grids with a
  CD ≠ 0 are transformed; if pyNastran can't transform a grid's CD, that grid is excluded
  and counted in the report.
- Mass-property targets must be about the CG in the FEM's basic frame. The tool doesn't
  transform CAD frames.
- The thermal check overwrites every CTE, so it tests model consistency, not the real
  material response.
- Shell element forces are per unit length; the unit-displacement threshold for them is a
  judgment call (decision #5).
- **Nothing can run Nastran here.** Parsers get tested against your sample .f06/.op2 files;
  the end-to-end run is untested until you run it.

## Status

**Plan approved** (2026-11-04). Not implemented yet; waiting on the full model deck upload
for deck-writing tests.

Checksum question resolved: FEMAP-exported decks end with a checksum (`ENDDATA b482e8c1`),
but the user confirmed a hand-written deck with no checksum runs on the workstation's
Femap-bundled Simcenter Nastran. Python-written decks are fine, and the architecture stands.
The tool must still read FEMAP decks that carry the trailing checksum on the `ENDDATA`
line; check that pyNastran tolerates it, and strip it on read if not.

First-run checklist (on your PC, with the sample model):
1. `pip install pyNastran reportlab`, then `python -m fem_checks` opens the GUI.
2. Diff the model's GPWG mass/CG/I(S) in `01_freefree.f06` against your own free-free
   .f06. They must match to every printed digit. If they don't, the bulk rewrite lost
   something.
3. The auto-picked mount grid: confirm it's sensible (expect 199999 or a neighbor) and
   that the report's CG offset matches the GPWG CG.
4. The free-free eigenvalues and GROUNDCHECK sets match `freefree_check-0000.f06`.
5. The unit-displacement SPCFORCE totals match `ud_check-0000.f06` (≤ 1.4e-4), and
   the rotation subcases show rigid-body translations (spot-check 2 grids by hand:
   u = θ × (r − r_m)).
6. The gravity OLOAD T1 = 733.7038 and SPCFORCE T1 = −733.7038, as in
   `1g_unit_load_check-0000.f06`.
7. Thermal: spot-check one far grid: u ≈ 1e-5 × (r − r_m). Then look at the top-20
   list in FEMAP; the elements named should be near RBE2s or CBUSHes.
8. Make one check fail deliberately (e.g. add a CELAS2 to ground). GROUNDCHECK G-set must
   FAIL and the report's top table must show it.
