# fem_checks: Installation and First Run

Step-by-step setup of the FEM validation checks on a new Windows workstation, and a first
run. Allow about 30 minutes, most of it downloads.

The tool is one self-contained folder, `fem_checks`. It holds the code, documentation,
reference PDFs, reference model, setup and launch scripts, and, after setup, its own
Python environment. Paths below are relative to that folder.

## 1. Prerequisites

| Item | Notes |
|---|---|
| Windows 10/11 workstation | |
| FEMAP | Only to export the model as a Nastran deck. |
| Simcenter Nastran, installed and licensed | The FEMAP-bundled solver works (`C:\Program Files\Siemens\Femap <version>\NASTRAN\bin\nastran.exe`), as does a Simcenter 3D install (`...\Simcenter3D_<version>\NXNASTRAN\bin\nastran.exe`). Verified with Simcenter Nastran 2306 and 2512. |
| The `fem_checks` folder | From the team's git repository, or a copy or ZIP from the tool owner (§2). |
| Internet access to `pypi.org` and `files.pythonhosted.org` | For the one-time package download. On a company network these may need to be allowed by IT (see §7). |
| About 1.5 GB of disk | Python, packages and the folder's reference files. |

## 2. Get the folder

Place it in a local folder that is **not** synchronized by OneDrive or a similar service
(the Python environment holds thousands of small files), for example `D:\Tools\fem_checks`.

**The folder must be named `fem_checks`.** Python finds the tool by that name, so a folder
renamed to e.g. `fem_checks-main` (as GitHub's ZIP download names it) must be renamed back.

Choose one:
- **Git** (recommended: easy updates). Clone the repository with GitHub Desktop, VS Code
  (*Ctrl+Shift+P > Git: Clone*) or `git clone`. If the repository holds other folders too,
  use the `fem_checks` folder inside it.
- **Copy or ZIP** from the tool owner. Extract it and check the folder name.

## 3. Install Python 3.12

Python **3.12** is required. 3.13 and later cannot install pyNastran 1.4.1, because it needs
numpy < 2, and numpy 1.x has no Windows packages for 3.13 or later.

1. Download the *Windows installer (64-bit)* of the latest **3.12.x** from
   <https://www.python.org/downloads/windows/>, listed under "Stable Releases". The large
   button at the top of python.org offers a newer version; do not use it.
2. Run the installer and tick **Add python.exe to PATH** on the first screen.
3. Choose **Customize installation**. Under *Optional Features* make sure **pip**,
   **tcl/tk and IDLE** (needed by the GUI) and **py launcher** are ticked.
4. Under *Advanced Options* the defaults are fine. Without administrator rights, untick
   *Install for all users*.
5. If offered, click **Disable path length limit**.

Check in a new Command Prompt: `py -3.12 --version` prints `Python 3.12.x`.

## 4. Run the setup

In the `fem_checks` folder, double-click **`setup_fem_checks.bat`**. It:

1. finds Python 3.12 and creates a private environment in `fem_checks\.venv` (nothing is
   installed into the system Python);
2. installs pyNastran, reportlab, Jupyter and pytest;
3. registers the Jupyter kernel "fem_checks (.venv)";
4. writes VS Code settings and run configurations into `fem_checks\.vscode` (keeping any
   existing ones);
5. finds `nastran.exe` and pre-fills it in the GUI;
6. runs the self-tests.

It ends with **"Setup complete."** It is safe to run again at any time.

## 5. First run

1. **Export the model from FEMAP.** *File > Export > Analysis Model*, or export from the
   Analysis Set Manager. Any analysis set will do: the tool keeps only the bulk data and
   replaces the executive and case control with its own. Note the ID of the model's real
   constraint (SPC) set if you want the constrained-modes check.
2. **Start the tool:** double-click **`run_fem_checks.bat`** in the `fem_checks` folder.
   Or, in VS Code, open the `fem_checks` folder (*File > Open Folder*) and use *Run > Start
   Debugging > FEM checks: GUI*.
3. **Fill in the Run tab:**
   - **Model deck:** the exported `.dat`;
   - **Nastran executable:** `nastran.exe`, pre-filled by the setup. Use `nastran.exe`, not
     `nastranw.exe`;
   - **Output folder:** leave blank for `<deck name>_fem_checks` next to the deck;
   - **Unit system:** the model's units;
   - **Mount grid:** leave blank to pick the grid nearest the CG, or enter one;
   - **Real constraint SPC set ID:** required for constrained modes;
   - **Checks:** the thermal soak is only for thermal-distortion models.
4. Optionally enter CAD mass properties on the **Mass targets** tab.
5. Click **Run checks**. Nastran runs once per check (seconds to a few minutes each). When
   it finishes, the report opens.

Results are in the output folder:
- `<model>_fem_checks_report.pdf`, the report;
- `fem_checks.log`, the run log, which ends with one line per check;
- the check decks and their Nastran output.

The GUI remembers the last settings. A run can be repeated without the GUI by dragging its
`fem_checks.json` onto `run_fem_checks.bat`, or from a Command Prompt:
`run_fem_checks.bat "<output folder>\fem_checks.json"`. Add `--no-run` to rebuild the
report from existing outputs without running Nastran.

## 6. Reading the results

Page 1 of the report lists each check with PASS, FAIL, INFO, ERROR or SKIPPED.
**Appendix A (Method)** explains what each check runs, its criteria with the values used,
and the reasoning. ERROR means a Nastran run did not finish. It is not a model verdict:
see Appendix B, the log, and that run's `.f06` (search for `FATAL`).

## 7. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Setup: "No Python 3.10 - 3.12 found" | Python 3.12 not installed, or installed without *Add to PATH* / *py launcher*. Re-run the installer (*Modify*), or close and reopen the folder window and try again. |
| Setup or launch: "No module named fem_checks" | The folder is not named `fem_checks` (e.g. `fem_checks-main` from a ZIP). Rename it to `fem_checks`. |
| Setup: pip errors mentioning proxy, SSL or connection | The company network blocks `pypi.org` / `files.pythonhosted.org`. Ask IT to allow them, or from the folder *above* `fem_checks` run `fem_checks\.venv\Scripts\python -m pip install --proxy http://<proxy:port> -r fem_checks\requirements.txt`, then re-run the setup. |
| Setup: "tkinter missing" | Python was installed without *tcl/tk and IDLE*. Re-run the installer, choose *Modify*, and tick it. |
| Setup: test failures | Send the console output to the tool owner. |
| `run_fem_checks.bat`: "The Python environment is missing" | Run `setup_fem_checks.bat` first. If the folder was moved, delete `fem_checks\.venv` and run the setup again (a Python environment cannot be moved). |
| A check shows ERROR with "no .f06 written" in under a second | `nastranw.exe` was selected; select `nastran.exe` in the same folder. |
| "Permission denied" on the report | The previous report is open in a PDF viewer. The tool writes a time-stamped copy instead; close the viewer to keep the usual name. |
| A check shows ERROR after a normal-length run | Nastran stopped with a FATAL message. The log lists it under the run's deck name; the `.f06` has the details. |
| VS Code *Problems* tab full of warnings | Code-checker warnings, not run errors. Open the `fem_checks` folder itself in VS Code, and select its `.venv` interpreter (*Ctrl+Shift+P > Python: Select Interpreter*). |

When asking for help, send the **`Results:` block at the end of `fem_checks.log`** and, for
an ERROR, the FATAL message from the run's `.f06`.

## 8. Updating or moving

- **Update with git:** *Sync* (or `git pull`). Then run `setup_fem_checks.bat` once more; it
  installs anything new and re-runs the self-tests.
- **Update from a copy or ZIP:** replace the folder's contents with the new version, keeping
  `.venv`, then run the setup.
- **Move the folder:** move the whole `fem_checks` folder, delete `fem_checks\.venv`, and run
  the setup again. A Python environment has its location built in and does not survive a
  move.
- **Upgrading from the earlier layout** (setup script and `.venv` one level above
  `fem_checks`): delete the old `.venv` and `.vscode` folders next to `fem_checks`, run
  `fem_checks\setup_fem_checks.bat`, and open the `fem_checks` folder itself in VS Code.
