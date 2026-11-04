# FEMAP_API

FEMAP API macros and Python tools for model building, model checking and post-processing
with FEMAP and Simcenter Nastran.

## Contents

| Path | Contents |
|---|---|
| `fem_checks/` | **FEM validation checks**: a Python tool that runs the standard FEMCI model checks (free-free modes + GROUNDCHECK, constrained modes, unit enforced displacement, unit gravity, thermal soak, mass properties) on a FEMAP-exported model in Simcenter Nastran and writes one PDF report. Start with [`fem_checks/README.md`](fem_checks/README.md). |
| `macros/` | FEMAP API macros (WinWrap Basic), run in FEMAP via *File > Programming > API Programming*. |
| `docs/` | Research and design notes for the macros and tools, and presentations. |
| `tools/` | Stand-alone scripts. |
| `reference/` | Reference documents: FEMAP API Guide 2025 (`api.pdf`), Simcenter Nastran Quick Reference Guide 2025 (`QRG.pdf`), NASA GSFC FEMCI *The Book* (`FEMCI-The-Book.pdf`). |
| `setup_fem_checks.bat` | One-time workstation setup for `fem_checks`. |

## Macros

| Macro | Function | Design notes |
|---|---|---|
| `make_bolt_connections.bas` | Builds fastener connections for a whole bolt pattern: pairs the bolt center points on each side of a joint, finds the plate element the bolt axis pierces on each shell, builds a shape-function-weighted RBE3 spider on each, and joins them with a CBUSH. | `docs/*bolt-connections.md` |
| `write_group_includes.bas` | Writes one mesh INCLUDE file per selected group, plus shared material, property, coordinate system, load and constraint files and a master include. | `docs/*group-includes.md` |
| `sum_strain_energy.bas` | Sums element strain energy for a named element selection over one or more output sets, with its share of the model total; output to Excel or CSV. | `docs/*strain-energy.md` |
| `apply_thermal_datasurface.bas` | Applies an x, y, z, T point cloud to a mirror's nodes as nodal temperatures through an arbitrary 3D data surface. | `docs/*datasurface.md`, `docs/*curved-surface-mapping.md` |
| `show_point_cloud.bas` | Displays the thermal point cloud as geometry points to check its placement on the model. | `docs/*femap-viz.md` |

## Tools

| Script | Function | Design notes |
|---|---|---|
| `tools/npz_to_3D.py` | Converts a 2D (x, z) transient thermal result into a vertex-centered 3D (x, y, z, T) point cloud for one circular mirror, as a table FEMAP reads as an arbitrary 3D scalar map. | `docs/*npz-3d-mapping.md` |

## Conventions

Each macro or tool has a research document and a design (plan) document in `docs/`
(packages keep theirs in their own `docs/` folder). Macro source files carry a header with
purpose, method, assumptions, usage and the API calls used.
