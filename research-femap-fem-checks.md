# Research: FEM checkout suite ("I-book checks") in one macro

**Status:** idea captured 2026-11-02. API research NOT started — no api.pdf line refs yet.
Next step is the research pass below, then the user answers the open questions inline.

## Goal (as stated)

One macro that sets up, runs and reports the standard FEM model checks:

1. Free-free modes, first 10 (expect 6 rigid-body modes ~0 Hz, then elastic).
2. Ground check (Nastran `GROUNDCHECK`) — strain energy under rigid-body motion.
3. Unit enforced displacements and rotations (T1-T3, R1-R3) — model should move rigidly,
   no strain energy / stress.
4. Unit gravity check (1g in X, Y, Z) with OLOAD vs SPCFORCE resultant comparison and
   mass check.
5. 1 K uniform thermal soak — free expansion, check for spurious stress (rigid elements,
   mismatched CTEs, over-constraint).
6. One output report covering all runs.

"Set up and go," though it may need more than one analysis run.

## What FEMCI "The Book" says (confirmed in FEMCI-The-Book.pdf)

Line refs are into `pdftotext -layout FEMCI-The-Book.pdf femci.txt` (`femci ~line`).
Section "Validity Checks" (femci ~1880). The Book lists **four** mathematical checks: your
ground check lives inside the free-free check, and OLOAD/SPCFORCE inside the gravity check.
The first three are for every statics/dynamics model; the thermal check is only for
models used in thermal distortion analysis (femci ~1891).

| Check | Constraint (per Book) | Pass criteria (per Book) |
|---|---|---|
| Unit enforced disp/rotation, SOL 101, 6 subcases (femci ~1953) | `SPC1 123456` on **one grid**, and `SPCD` 1.0 on that same grid in each DOF. That grid has no other SPCs and should be near the CG (~1966) | Translation subcases: 1.0 in the input DOF, 0.0 in the other 5. Rotation subcases: 1.0 rad about the input axis, other two rotations 0 (translations nonzero, rigid-body). Element forces and grid-point force balance ≈ 0, max **0.1 lb / 1.0 in-lb** (~1965) |
| Free-free modes + GROUNDCHECK, SOL 103 (femci ~2082) | Remove all external SPCs. The `SPC=100` note is only for normal (drilling) plate rotations, not a base mount | ≥ 6 rigid-body modes at **≤ 1.0E-04 Hz** (~2095). `GROUNDCHECK(SET=ALL)=YES` prints G/N/F/A-set strain energy with PASS/FAIL against a limit (default: largest K term / 1e10). A G-set error propagates to later sets; `PARAM,AUTOSPC,YES` can falsely fail the A-set (~2127) |
| Unit gravity X/Y/Z, SOL 101, 3 subcases (femci ~2231) | **Real mount** ("structure held at base") | SPC forces only at mount points. Their sum equals total weight in the load direction, ~0 in the others. No large displacements. `PARAM,GRDPNT,0` for the weight summary (~2270) |
| Unit temperature increase, SOL 101 (femci ~2276) | **Kinematic mount**: "one GRID point constrained in all 6 DOFs" (~2289, ~2297) | Displacements match theory, α·L·ΔT. Grid-point force balance is zero everywhere, and SPC forces appear only at the mount. Setup: `TEMPD` = Tref + 1 (~2315). **All MAT cards set to the same CTE and same Tref** (~2319). RBE2s not joined by coincident nodes should not be used (~2318) |

Implications:
- **Thermal soak is kinematically mounted, not unconstrained** (resolves Q1a). Mechanically
  a single-grid 123456 mount is still free expansion, so SPC forces should be ~0.
- The Book compares against the theoretical α·L·ΔT using **THDEF**, a legacy program on
  a GSFC VAX ("merger", ~2285) that we don't have. With a uniform α and a 123456 mount at
  grid m, the theory is just u = α·ΔT·(r − r_m) in basic coordinates. That's easy to
  compute in Python per grid, and replaces THDEF.
- The thermal check needs a **modified deck**, with every CTE and Tref overwritten. The
  tool must do that to a copy of the deck, never to the model.
- Same grid for the unit-enforced mount, the thermal kinematic mount and the
  `GROUNDCHECK(GRID=...)` reference: one near the CG (from GPWG).
- The Book's units are in-lb (`GRAV 386.4` with `PARAM,WTMASS,0.00259`, ~2272). The tool
  must pick g and the force/moment thresholds to match the model's units.
- The Book doesn't mention OLOAD explicitly. Comparing the OLOAD resultant against the
  SPCFORCE resultant and the GPWG weight is a stricter version of its "sum = total
  weight" check, so keep all three.
- Its decks reference SOL 24 / MSC; use SOL 101 / 103 for both MSC and Simcenter.
- Extra checks the Book lists for pre/post-processing (free edges, coincident
  nodes/elements, bar orientation, plate distortion/normals, mass) start at femci ~1897.
  They're out of scope for now (see Q9).

## Key API findings (confirmed in api.pdf)

_TBD — research pass. Things to find:_
- Analysis set creation and editing (`feAnalysisMgr`): solution type, output requests,
  case-control text, and multiple subcases.
- Kicking off a Nastran run from the API and waiting for it to finish, then reading
  results back.
- Creating load sets: body acceleration (gravity), uniform temperature / default
  temperature, enforced displacement.
- Creating constraint sets / single-node constraints programmatically.
- Output vector IDs (via `feResultsIDQuery`) for eigenvalues, SPC force, applied load,
  strain energy, stress.
- Whether `GROUNDCHECK`, `PARAM,GRDPNT` and OLOAD/SPCFORCE resultants are exposed
  anywhere in FEMAP results, or only in the .f06.

## Engineering notes (first thoughts, to verify)

- **Run count:** realistically two runs, not five. SOL 103 free-free (no SPCs) for modes
  and ground check; one SOL 101 with subcases for gravity X/Y/Z, thermal soak and the six
  unit enforced motions. Fewer runs → faster, one .f06 to parse for the static checks.
- **Constraints differ per check:** the free-free run has none. Unit displacement and
  thermal soak need a *statically determinate* (kinematic) mount — e.g. one node fixed
  123456, or a 3-2-1 set — or the checks are meaningless. Gravity can use the real BCs or
  the determinate mount. The macro needs the user to pick that mount node(s).
- **Unit displacement subcase:** enforced SPCD on the mount node with everything else free.
  Expect zero strain energy, zero stress, displacement field = rigid-body motion. Unit
  rotation at a node far from the CG gives big translations elsewhere — compare against
  the rigid-body expectation, not just "≈ 1".
- **Thermal soak:** only stress-free if CTE is uniform across the model and rigid
  elements are allowed to expand (RBE2/RBE3 thermal behavior, `ALPHA` on the rigid
  element). Mixed-material models will show real stress, so pass/fail needs a threshold
  or an "expect stress here" group.
- **Report data mostly lives in the .f06:** GROUNDCHECK, OLOAD RESULTANT, SPCFORCE
  RESULTANT, GPWG mass summary, and the EPSILON / max-ratio diagnostics are printed in
  the .f06 and probably not imported into FEMAP results. The report likely needs an .f06
  text parser alongside `feResults` for eigenvalues / displacements / stress.
- **Model hygiene:** this creates load sets, constraint sets and analysis sets. Per repo
  rules nothing changes silently — decide whether to keep them (named clearly, e.g.
  `CHK_*`) or delete after the run.

## Open design questions (for the plan)

0. **Architecture:** FEMAP API macro vs Python (pyNastran) vs hybrid. FEMAP exports one
   base .bdf; Python builds the check decks, runs Nastran, parses OP2/.f06 and writes the
   report?
   >> (2026-11-03, chat) user leaning Python; asked for a recommendation. Recommended:
   Python end to end, FEMAP only exports the base deck. Awaiting confirmation.
1. "Unit grab check" — is this the 1g gravity check (X, Y, Z separately)? Any combined or
   +/- direction cases needed?
   >> yes, gravity check. (+/- and combined cases still open.)
1a. Thermal soak constraint: user thinks it should be unconstrained, wants it checked
   against the I-book.
   **Resolved by FEMCI (~2289, ~2297): kinematic mount, one grid 123456.** See table above.
   Note: a truly unconstrained SOL 101 is singular and Nastran will fail, unless you use
   inertia relief (`PARAM,INREL,-2`). In practice "unconstrained" means a statically
   determinate mount whose SPC forces come out ~0. That's mechanically identical to free
   expansion, and the ~0 reactions are themselves part of the check.
2. Pass/fail criteria: what thresholds for rigid-body mode frequency (e.g. < 1e-3 Hz?),
   ground-check strain energy, OLOAD vs SPCFORCE % difference, thermal soak max stress?
   Or is the report data-only with no pass/fail?
   Book defaults: RBM ≤ 1e-4 Hz; GROUNDCHECK's own PASS/FAIL; unit-enforced forces
   ≤ 0.1 lb / 1.0 in-lb; gravity reactions = GPWG weight; thermal displacements = α·L·ΔT.
   The Book gives no % tolerance for the gravity or thermal checks, so those still need one.
3. Kinematic mount for unit displacement and thermal soak: user picks one node (123456),
   or a 3-2-1 set, or the macro picks the node nearest the CG?
   Book: one grid, 123456, near the CG. Open: user-picked, or auto-picked as the grid
   nearest the GPWG CG?
4. Gravity check: run against the model's real constraints, or the kinematic mount?
   Book: real mount. The tool needs to know which SPC set is the real one.
5. Report format: text file, CSV, FEMAP message window, or something richer (HTML /
   Word)? Should it include plots/screenshots?
6. Keep the generated `CHK_*` loads/constraints/analysis sets after running, or clean up?
7. Should the macro run Nastran itself and wait, or just build the analysis sets and let
   the user run them, then a second pass parses results?
8. Any Nastran flavor constraints (MSC vs Simcenter/NX)? GROUNDCHECK and some PARAMs
   differ slightly.
9. Anything else from your I-book checklist that belongs here (mass/CG vs expected,
   element quality, free edges, coincident nodes, `PARAM,AUTOSPC` / `MAXRATIO` review)?
