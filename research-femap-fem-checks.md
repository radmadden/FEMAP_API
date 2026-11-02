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

1. "Unit grab check" — is this the 1g gravity check (X, Y, Z separately)? Any combined or
   +/- direction cases needed?
2. Pass/fail criteria: what thresholds for rigid-body mode frequency (e.g. < 1e-3 Hz?),
   ground-check strain energy, OLOAD vs SPCFORCE % difference, thermal soak max stress?
   Or is the report data-only with no pass/fail?
3. Kinematic mount for unit displacement and thermal soak: user picks one node (123456),
   or a 3-2-1 set, or the macro picks the node nearest the CG?
4. Gravity check: run against the model's real constraints, or the kinematic mount?
5. Report format: text file, CSV, FEMAP message window, or something richer (HTML /
   Word)? Should it include plots/screenshots?
6. Keep the generated `CHK_*` loads/constraints/analysis sets after running, or clean up?
7. Should the macro run Nastran itself and wait, or just build the analysis sets and let
   the user run them, then a second pass parses results?
8. Any Nastran flavor constraints (MSC vs Simcenter/NX)? GROUNDCHECK and some PARAMs
   differ slightly.
9. Anything else from your I-book checklist that belongs here (mass/CG vs expected,
   element quality, free edges, coincident nodes, `PARAM,AUTOSPC` / `MAXRATIO` review)?
