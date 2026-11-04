# Research: mapping the thermal cloud onto a curved mirror surface

## The problem
The thermal field is a flat through-thickness column: `T = T(depth)`, uniform across the face.
Real mirror surfaces are convex/concave, so the optical surface sits at `z = sag(x,y)` instead
of `z = 0`. Your chosen (interim) rule: **shift each through-thickness column straight down by
the local sag** — a node whose axial depth below the curved surface is `d` gets the flat
column's `T(d)`. Depth is measured along the mirror axis (z), not the surface normal. This is
exactly what `import_point_cloud.bas` Mode 2 already does for geometry points:
`z = sag(r) + Z_SIGN * depth`.

## The sag
Conic surface sag at radius `r = sqrt(x^2 + y^2)`:
```
sag(r) = c*r^2 / (1 + sqrt(1 - (1+K)*c^2*r^2)),   c = 1/ROC
```
`ROC` = vertex radius of curvature (ROC = 0 / infinite → flat, sag = 0); `K` = conic constant
(0 sphere, -1 parabola). `Z_SIGN` sets which way depth goes into the substrate. These three
constants are already defined and used in `import_point_cloud.bas`.

## What the FEMAP API offers (api.pdf)
- **Analytic:** no API needed — the conic formula above (lift it from `import_point_cloud.bas`).
- **From real geometry:** `Surface` object methods `XYZToParam` (project a point to the
  surface's u,v) and `ParamToXYZ` (u,v → point) let us find the surface point near any (x,y),
  i.e. read the sag straight from the modeled optical surface. `NormalAtXYZ` gives the local
  normal if we ever want normal-direction depth. `feMeasureDistanceToPlane(p, planePt,
  planeNorm, dist)` gives axial depth to the vertex plane.
- Data-surface types include Parametric-over-a-surface (types 4/5), but those parameterize the
  in-plane field, not through-thickness sag — not the right tool here.

## Options

### Option 1 — Un-sag the lookup in the apply macro, analytic conic sag  (recommended)
Keep the **flat** cloud exactly as is. In `apply_thermal_datasurface.bas`, add `MIRROR_ROC`,
`MIRROR_K`, `Z_SIGN` constants. For each node `(x,y,z)`, compute `sag = conic(r)` and evaluate
the flat data surface at the **shifted** coordinate `(x, y, z - Z_SIGN*sag)` so the node's axial
depth below the curved surface indexes the flat column. Nearest neighbor then returns
`T(depth)` — your "delta-z moved down and propagated through thickness," exactly.
- **Pros:** one flat cloud serves every surface; switch surfaces by changing ROC/K; ~3-line
  change (an offset before `GetData`); reuses the proven conic formula; no regen of the cloud.
- **Cons:** assumes a conic you can specify; axial (not normal) depth — you said that's fine.
- **Needs:** `ROC` and `K` per mirror (and the sign convention).

### Option 2 — Bake the sag into the Python cloud
In `npz_to_3D.py`, offset each generated point's z by `sag(x,y)` so the cloud itself is curved;
the apply macro maps curved→curved unchanged.
- **Pros:** the cloud physically sits on the curved surface (also nicer for the geometry-check
  viz); apply macro untouched.
- **Cons:** the cloud is tied to one surface shape; ROC/K now live in Python; regenerate per
  surface; the viz skeleton would curve too.
- **Needs:** `ROC`/`K` in Python.

### Option 3 — Pull the sag from the real FE/CAD surface (no conic assumption)
Same shift as Option 1/2, but get `sag(x,y)` from the actual optical surface geometry instead
of a formula: project each node onto the mirror `Surface` (`XYZToParam` → `ParamToXYZ`) or
measure its axial distance to the vertex plane, and use that as the sag.
- **Pros:** handles ANY surface — asphere, measured, freeform — exactly as modeled; no formula.
- **Cons:** more macro code; must identify the optical `Surface` ID (or a front-surface node
  group); projection returns the nearest point (≈ axial for slow mirrors, small error otherwise).
- **Needs:** the optical surface present as FEMAP geometry (a Surface ID), or a front-surface
  node set to read z from.

## Recommendation
**Option 1 now.** It matches your stated axial delta-z rule, is the smallest change (a
coordinate offset before the data-surface lookup), reuses the conic sag already written, and
keeps a single reusable flat cloud. It also generalizes cleanly later: swap the analytic
`sag()` for the Option 3 geometry projection when you need real/aspheric surfaces, with the
rest of the macro unchanged. Use Option 2 only if you specifically want the viz cloud to sit on
the curve as well.

## To proceed I need
- `ROC` and conic constant `K` for the mirror(s) (or "it's a sphere, R = …").
- Sign convention: is the optical surface facing +Z with glass behind it (so depth goes -Z,
  `Z_SIGN = -1`, as in the old macro)? We'll confirm with a one-node check on first run.
- Whether the optical surface also exists as FEMAP geometry (decides if Option 3 is on the table
  without extra setup).
