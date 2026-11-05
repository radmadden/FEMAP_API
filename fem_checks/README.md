# fem_checks: FEM validation checks for Simcenter Nastran

Runs the standard finite element model validity checks (NASA GSFC FEMCI *The Book*) on a
model exported from FEMAP, and writes one PDF report with a PASS / FAIL verdict per check,
all supporting data, and the relevant Nastran output.

| Check | Run | PASS when |
|---|---|---|
| Free-free modes + GROUNDCHECK | SOL 103, no constraints | rigid-body / first elastic frequency ≤ 1e-4 and every GROUNDCHECK set passes |
| Constrained modes | SOL 103, model constraints | report only |
| Unit enforced displacement / rotation | SOL 101, 6 subcases | no reaction, rigid-body motion, no element force |
| Unit gravity X / Y / Z | SOL 101, 3 subcases | OLOAD = −SPCFORCE = mass × g |
| Thermal soak, +1° (thermal-distortion models) | SOL 101 | free expansion, no stress |
| Mass, CG, inertia (optional) | from the free-free run | within the entered tolerances |

## Setup (once per workstation)

New users: follow **[`docs/installation.md`](docs/installation.md)**. It covers prerequisites,
getting the code, installing Python, the first run, troubleshooting and updating. In short:

1. Install **Python 3.12** from python.org (Windows installer, 64-bit). During installation
   select *Add python.exe to PATH*, *py launcher* and *tcl/tk and IDLE*.
   Python 3.13 or later will not work (pyNastran 1.4.1 requires numpy < 2).
2. Double-click `setup_fem_checks.bat` in this folder. It creates a private Python
   environment (`.venv`, inside this folder), installs the required packages, configures
   VS Code, finds `nastran.exe`, and runs the self-tests. It ends with "Setup complete."

This folder is self-contained (code, docs, reference PDFs, reference model, scripts) and can
be copied or moved as a unit. It must keep the name `fem_checks`. After a move, delete
`.venv` and run the setup again.

## Running the checks

1. In FEMAP, export the model as a Nastran deck (`.dat`). Any analysis set will do; the
   tool replaces the executive and case control and keeps the bulk data.
2. Start the tool: double-click `run_fem_checks.bat`. Or open this folder in VS Code and
   use *Run > Start Debugging > FEM checks: GUI*.
3. On the **Run** tab, select:
   - the model deck and the Nastran executable (`nastran.exe`);
   - the unit system;
   - the mount grid (leave blank to pick the grid nearest the CG);
   - the model's real constraint SPC set ID (needed for constrained modes);
   - the checks to run.
4. Optionally enter CAD mass properties on the **Mass targets** tab.
5. Click **Run checks**. Nastran runs once per check; the report opens when finished.

Everything is written to `<deck name>_fem_checks\` next to the deck:
- `<model>_fem_checks_report.pdf`, the report (the model file name is also in its title and
  on every page);
- `01_<model>_freefree.dat` … `05_<model>_thermal.dat` and their Nastran output;
- `fem_checks.json`, the settings used;
- `results.json`, every number in the report;
- `fem_checks.log`, the run log.

**Re-evaluate existing outputs (no Nastran)** rebuilds the report from results already in
the output folder. To repeat a run without the GUI, drag its `fem_checks.json` onto
`run_fem_checks.bat` (add `--no-run` from a Command Prompt to skip Nastran).

## Reading the report

Page 1 lists every check with its verdict. Each check then has its criteria, data tables,
a chart, and the verbatim `.f06` blocks the verdict was read from. Cells shaded the same
color are the values a criterion compares with each other. Appendix A (Method) explains,
for each check, what was run, the criteria with the values applied, and why. ERROR means
Nastran did not finish that run (see Appendix B and the run's `.f04`/`.log`); it is not a
model verdict.

## Documentation

| Document | Contents |
|---|---|
| `docs/installation.md` | Installation, first run, troubleshooting and updating, for a new user |
| `docs/design.md` | Design description: scope, design decisions with rationale, algorithm, outputs, limitations, verification status and checklist |
| `docs/background.md` | FEMCI requirements, Simcenter Nastran and pyNastran findings, reference model observations |
| `docs/fem_checks_design.ipynb` | Step-by-step executable walkthrough on the reference model |
| `reference/` | FEMCI *The Book* and the Simcenter Nastran Quick Reference Guide, cited as `femci ~line` / `qrg ~line` |
| `reference_model/` | Reference model deck and Nastran outputs used for verification |

Tests: in VS Code's *Testing* panel, or from the folder above this one:
`fem_checks\.venv\Scripts\python -m pytest fem_checks\tests`.
