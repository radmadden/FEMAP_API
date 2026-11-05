# FEM Validation Checks: Background and Findings

Companion documents (this folder): `design.md` (design description) and
`fem_checks_design.ipynb` (executable walkthrough). Reference PDFs are in `reference/` at the
repository root; the reference model and its outputs are in `fem_checks/reference_model/`.

## 1. Purpose

The `fem_checks` tool performs the standard finite element model validity checks on a
Simcenter Nastran model with a single action. Given a model deck exported from FEMAP
(`.dat`/`.bdf`) and the local Nastran executable, the tool writes the check decks, runs
Nastran, parses the `.f06` and `.op2` results, and issues one report with a PASS / FAIL
verdict per check and all supporting data.

Checks performed:
1. Free-free normal modes with a stiffness equilibrium check (`GROUNDCHECK`).
2. Constrained normal modes on the model's boundary conditions (report only).
3. Unit enforced displacement and rotation, T1-T3 and R1-R3.
4. Unit gravity in X, Y and Z, with an OLOAD vs SPCFORCE resultant comparison and a weight
   check.
5. Unit temperature increase (thermal soak) on a kinematic mount.
6. Model summary: node and element counts, element quality, and an optional comparison of
   mass, CG and inertia against CAD or target values.

The tool is written in Python. FEMAP is used only to export the model deck.

## 2. Reference sources

| Reference | Use | Line references |
|---|---|---|
| NASA GSFC FEMCI, *The Book*, section "Validity Checks" (`reference/FEMCI-The-Book.pdf`) | Check definitions and pass criteria | `femci ~line` = line in `pdftotext -layout FEMCI-The-Book.pdf femci.txt` |
| Simcenter Nastran Quick Reference Guide, 2025 (`reference/QRG.pdf`) | Case control and bulk data syntax, defaults | `qrg ~line` = line in `pdftotext -layout QRG.pdf qrg.txt` |
| pyNastran 1.4.1 source | Deck and OP2 reading | file:line in the installed package |
| Reference model `BD IRAD V2` (`fem_checks/reference_model/1G_unit_load_check-0000.dat`) and its outputs (`freefree_check-0000.f06`, `ud_check-0000.f06/.op2`, `1g_unit_load_check-0000.f06/.op2`) | Parser development and verification | n/a |

The reference model has 24,851 grids; CHEXA, CPENTA, CQUAD4, CTRIA3, CBEAM, CBUSH, RBE2,
RBE3 and CONM2 elements; mass 1.900295 lbf·s²/in; and units in-lbf-s.

## 3. FEMCI validity checks

*The Book* (femci ~1880) defines four mathematical validity checks. The ground check is
part of the free-free check, and the reaction comparison is part of the gravity check. The
first three apply to all statics and dynamics models; the thermal check applies to models
used for thermal distortion analysis (femci ~1891).

| Check | Constraint | Book pass criteria |
|---|---|---|
| Unit enforced displacement / rotation, SOL 101, 6 subcases (femci ~1953) | `SPC1 123456` on one grid with `SPCD` 1.0 in each DOF in turn. The grid carries no other SPC and should be near the CG (~1966). | Translation subcases: 1.0 in the enforced DOF, 0.0 in the other five. Rotation subcases: 1.0 rad about the enforced axis, other rotations 0, translations follow rigid-body motion. Element forces and grid point force balance ≈ 0, at most **0.1 lb / 1.0 in-lb** (~1965). |
| Free-free modes + GROUNDCHECK, SOL 103 (femci ~2082) | No external SPCs | At least six rigid-body modes at **≤ 1.0E-04 Hz** (~2095). `GROUNDCHECK` reports strain energy per DOF set (G, N, F, A) with PASS/FAIL against a limit. A G-set error propagates to later sets; `PARAM,AUTOSPC,YES` can cause a spurious A-set failure (~2127). |
| Unit gravity X/Y/Z, SOL 101, 3 subcases (femci ~2231) | Mounted structure | SPC forces only at the mount, summing to the model weight in the load direction and ~0 in the others. `PARAM,GRDPNT,0` (~2270). |
| Unit temperature increase, SOL 101 (femci ~2276) | Kinematic mount: "one GRID point constrained in all 6 DOFs" (~2289, ~2297) | Displacements equal the α·L·ΔT theory; grid point force balance zero; SPC forces only at the mount. `TEMPD` = Tref + 1 (~2315). **All materials set to the same CTE and Tref** (~2319). No RBE2 between non-coincident grids (~2318). |

The Book compares the thermal displacements with the output of THDEF, a legacy GSFC
program (femci ~2285). With a uniform CTE and a single-grid 123456 mount the theoretical
field is u = α·ΔT·(r − r_m) for every grid, which the tool computes directly.

### 3.1 Other Book checks

The Book's "computer-aided" and "common sense" accuracy checks (femci ~1897-1950), and how
the tool treats each:

| Book check | Treatment |
|---|---|
| Mass reasonable (~1915) | Implemented: mass, CG and inertia against optional CAD or target values. |
| Plate distortion (~1906) | Implemented: element quality statistics and counts against Simcenter's default GEOMCHECK limits (section 5.4). |
| Singularities / AUTOSPC (~1944) | Implemented: AUTOSPC singularity count, EPSILON, and every FATAL/WARNING message from each run. |
| Units, WTMASS (~1920) | Implemented: the unit system is a run input; a missing `PARAM,WTMASS` is flagged when the unit system requires one. |
| Coincident nodes (~1903), free edges (~1901), CELAS between non-coincident grids (~1934) | Not implemented. A grounding spring is still detected as a GROUNDCHECK G-set failure. |
| RBE3 used as structure, oversized RBE2s (~1932) | Not implemented; requires engineering judgment. |
| Bar orientation, plate normals, coincident elements | Not implemented; requires visual inspection in FEMAP. |

## 4. pyNastran capabilities (version 1.4.1)

| Need | pyNastran API | Reference |
|---|---|---|
| Read a deck in the Simcenter dialect | `BDF(mode='nx')`, `read_bdf(...)` | `bdf/bdf.py:526,542`, `:4862` |
| Mass, CG and inertia without a solver run (WTMASS-aware) | `mesh_utils.mass_properties.mass_properties(model)` | `mass_properties.py:176` |
| Card and element counts | `model.card_count`, `model.get_bdf_stats()` | `bdf.py:3565` |
| Element quality (interior angles, skew, aspect, taper, warp, area ratio, edge length) | `element_quality(model)` | `delete_bad_elements.py:403` |
| Read OP2 in the Simcenter dialect | `OP2(mode='nx')` | `op2/op2.py` |
| OP2 results | `displacements`, `spc_forces`, `load_vectors`, `grid_point_weight`, `op2_results.force.*`, `op2_results.stress.*` | attributes of an `OP2()` instance |
| Output-system to basic transformation | `get_displacement_index_xyz_cp_cd`, `transform_displacements_to_global` | `bdf.py:3589`, `op2.py:1186` |

pyNastran 1.4.1 reads Simcenter 2512 OP2 files with the warning "nx version='2512' is not
supported"; every table used by the tool loads correctly.

The following tables exist only in the `.f06` and are read by the tool's own parser
(`fem_checks/f06.py`): GROUNDCHECK (UIM 7570), WEIGHTCHECK, OLOAD and SPCFORCE RESULTANT,
EPSILON (UIM 5293), the AUTOSPC singularity count, and FATAL/WARNING messages. The
eigenvalue and effective-mass tables are also read from the `.f06`.

pyNastran 1.4.1 requires numpy < 2. numpy 1.x has no Windows wheels for Python 3.13 or
later, so the tool requires Python 3.10-3.12.

## 5. Simcenter Nastran findings

### 5.1 Case control and bulk data (QRG)

- `GROUNDCHECK(PRINT,SET=(G,N,N+AUTOSPC,F,A),GRID=gid,THRESH=e,DATAREC=YES)=YES`
  (qrg ~18934). Must appear above the subcase level. Default limit = largest stiffness term
  / 1e10. `SET=N+AUTOSPC` zeroes the rows of AUTOSPC-constrained DOF (qrg ~18975).
- `WEIGHTCHECK(PRINT,SET=(...),GRID=gid,CGI=YES,MASS)=YES` (qrg ~31138). The `WEIGHT`/`MASS`
  describer selects the output units (default `WEIGHT`). For reduced sets the output
  includes the percentage change in rigid-body mass relative to the G-set, which detects
  mass lost or gained by MPCs and rigid elements. SOL 101 supports only `SET=G` (~31195).
- `MEFFMASS` (qrg ~22311), `GPFORCE` (~18256), `OLOAD` (~24073).
- `SPCD` (qrg ~119845) is selected by `LOAD=` in static solutions; the enforced DOF must
  also be constrained by an SPC/SPC1 entry selected by the `SPC` command (remark 3).
- `TEMPD` (qrg ~123383) is selected by `TEMPERATURE(LOAD)` / `TEMPERATURE(INITIAL)`. With
  `TEMPERATURE(INITIAL)` defined, linear thermal strain is ε = α(T_load − T_init) and MATTi
  properties are evaluated at T_init (qrg ~29940, remark 8).
- Rigid elements RBAR, RBE1, RBE2, RBE3 and RROD have an `ALPHA` field (RBE2: qrg ~112917;
  RBE3: ~112985). It is used only with `RIGID=LAGRAN` (qrg ~26012); the default
  `RIGID=LINEAR` computes no thermal load for rigid elements. Under `LAGRAN`, rigid element
  IDs must be ≤ 1e8. Rigid-element thermal strain is α·(AVGTEMP(LOAD) − AVGTEMP(INIT))
  (qrg ~112944).
- Defaults: `PARAM,AUTOSPC` YES in SOLs 101 and 103 (qrg ~35652); `GRDPNT` −1 (~37634);
  `WTMASS` 1.0 (~42411).

### 5.2 Observations from the reference model outputs

- **Rigid-body frequencies.** The six rigid-body modes lie between 5.3e-5 and 4.3e-4 Hz;
  the first elastic mode is 133 Hz. Four of the six exceed FEMCI's absolute 1e-4 Hz limit,
  while GROUNDCHECK passes every set by four to seven orders of magnitude. An absolute
  frequency limit does not scale with model size or units; the ratio of the largest
  rigid-body frequency to the first elastic frequency (3.2e-6 here) does. See design
  decision D1.
- **GROUNDCHECK output** follows the Book's format with module `GPWG1S` and an additional
  `N+AUTOSPC` set: a "MATRIX Kxx (X-SET)" header, the limit, and six rows of direction,
  strain energy and PASS/FAIL.
- **Resultant tables.** OLOAD and SPCFORCE RESULTANT print FX…MZ rows and a TOTALS row
  (T1…R3) per subcase, about the basic origin; "----" marks entries that do not apply.
- **Normalization of the gravity comparison.** In the 1g X subcase, T1 is 733.7038 in OLOAD
  and −733.7038 in SPCFORCE. Off-axis components are round-off (e.g. SPCFORCE 1e-8 against
  an OLOAD of exactly 0; R2 5.292096e-3 against −5.292085e-3). A per-component percentage
  test fails on this noise, so the comparison uses vector norms: |F_OL + F_SPC| / |F_OL|, and
  for moments |M_OL + M_SPC| / (|M_OL| + |F_OL|·L) with L the model size.
- OLOAD T1 = 733.7038 = 1.900295 × 386.1, closing the mass × g check.
- Unit-displacement SPCFORCE totals are ≤ 1.4e-4 in every component.
- EPSILON is ~1e-15 for every subcase; Nastran flags values above 1e-3.
- AUTOSPC constrains 51,879 singular DOF, the rotational DOF of solid-element grids. This
  is expected on solid models and is reported as a count.
- Two Simcenter versions appear in the reference outputs (2306 and 2512); the parsers key
  on table headings, not on the version banner.
- **Product-of-inertia sign.** WEIGHTCHECK/GPWG `I(S)` prints Ixy = +5.385. The rigid-body
  mass matrix gives MO(4,5) = −2.049 about the origin. Shifting to the CG:
  −2.049 + m·x·y = −2.049 + 1.9003 · 0.39607 · (−4.43269) = −5.385. MO therefore holds the
  inertia tensor (−∫xy dm), and **I(S) prints products as +∫xy dm**, the convention used by
  most CAD tools. Targets in tensor convention are sign-flipped before comparison.
- **Element force recovery under rigid rotation.** In the unit-displacement rotation
  subcases, CQUAD4 forces at element centroids are ≤ 1.6e-7, while CORNER (bilinear) values
  reach 33-82 lbf/in in about 88 elements. This is a recovery artifact under a 1 rad rigid
  rotation, not grounding. See design decision D6.
- The 1G deck of the reference model selects `NSM = 2` in case control; non-structural mass
  is therefore carried in case control, and the check decks must retain it.

### 5.3 FEMAP deck checksum

FEMAP writes a checksum after the `ENDDATA` keyword (e.g. `ENDDATA b482e8c1`). A
hand-written deck without the checksum was confirmed to run on the Femap-bundled
Simcenter Nastran, so decks written by the tool need no checksum. The tool strips the
checksum before pyNastran reads the deck.

### 5.4 Element quality limits

Simcenter applies default element-geometry limits on every run through `GEOMCHECK`
(qrg ~8150, remark 2), for example:

| Test | Default limit |
|---|---|
| CQUAD4 / CQUADR interior angle (Q4_IAMIN / Q4_IAMAX) | < 30° / > 150° |
| CQUAD4 skew (Q4_SKEW), taper (Q4_TAPER), warp factor (Q4_WARP) | < 30°, > 0.5, > 0.05 |
| CQUAD4 edge aspect ratio (QAD_AR) | > 100 |
| CTRIA3 maximum interior angle (T3_IAMAX) | > 160° |
| CHEXA / CTETRA aspect ratio (HEX_AR / TET_AR) | > 100 |

Exceeding a limit produces informational messages only, except the solid aspect-ratio
tests, which are FATAL by default. FEMAP's *Tools > Check > Distortion* uses its own
configurable tolerances. The tool counts elements beyond the Simcenter defaults for the
tests whose definition matches pyNastran's metrics (interior angles and edge aspect
ratio). Nastran's skew, taper and warp are defined differently from pyNastran's, so those
are reported as statistics only.

## 6. Engineering notes

**Mount grid trap.** The kinematic-mount grid must have rotational stiffness: it must lie
on a shell, beam, bar or bush, or be an RBE2 independent grid. A grid that touches only
solid elements has no rotational stiffness, so enforcing R1-R3 on it does not test the
model, and the rotation subcases would pass without meaning. The grid must also not be a
dependent (m-set) grid, since Nastran cannot constrain an m-set DOF. GROUNDCHECK's A-set
result becomes sensitive to the reference grid's distance from the CG (femci ~2223), so the
report states that distance.

**Deck handling.** The tool reads the FEMAP deck with pyNastran, discards its executive and
case control, and writes the bulk data once in large-field format. Each check deck adds its
own cards (SPC1, SPCD, GRAV, TEMPD, EIGRL, PARAMs) using a reserved ID range checked against
the model, and includes the shared bulk data. Case-control commands that change the model
itself (NSM, MPC, DMIG selection) are carried into every check deck.

**Rigid elements under RIGID=LAGRAN.** Under the default `RIGID=LINEAR`, dependent DOF of
rigid elements are eliminated before the solve. Under `LAGRAN` they remain in the solution
with Lagrange multipliers. An RBE2 that ties rotations to grids on solid elements only then
leaves zero-stiffness rotational DOF that AUTOSPC also constrains, and the conflicting
constraints produce near-singular pivots. On the reference model, RBE2 110225 (CM 123456,
256 of 352 dependent grids on solids only) stopped the thermal run with excessive pivot
ratios. Tying those grids in translation only is mechanically neutral and removes the
conflict; the thermal deck applies it.

**Thermal soak criteria.** With a uniform α, a uniform ΔT and a kinematic mount, the exact
solution is stress-free for any element mix, with u = α·ΔT·(r − r_m). Any stress therefore
indicates a modeling defect, and the criteria can be relative and unit-free:
- displacement: max |u_FEM − u_theory| / max |u_theory| ≤ 1e-3;
- stress: max von Mises / (E_max·α·ΔT) ≤ 1e-3, where E·α·ΔT is the fully restrained
  thermal stress;
- mount reaction: reported, not judged;
- the 20 most stressed elements are listed with the springs, bushes and rigid elements
  they touch, the usual sources of spurious thermal stress.

**Unit-dependent thresholds.** The Book's 0.1 lb and 1.0 in-lb become 0.445 N and
0.113 N·m (SI-m) or 0.445 N and 113 N·mm (SI-mm). Standard gravity is 386.0886 in/s²,
9806.65 mm/s² or 9.80665 m/s².

**Mass properties.** Targets are optional per quantity. Mass and inertia use percentage
tolerances; the CG uses an absolute length tolerance, because a percentage of a near-zero
CG coordinate is meaningless. Products of inertia are judged as a percentage of the largest
principal moment, because products are often near zero. Targets are taken about the CG in
the FEM basic frame; the tool does not transform CAD frames. FE values come from
`WEIGHTCHECK ... MASS` so the comparison is in mass units.
