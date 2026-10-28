# Plan: SAG_MODE extension for apply_thermal_datasurface.bas

Add a curved-surface option to the existing data-surface mapping macro. The flat cloud and the
data-surface/evaluate/apply logic are untouched; the only change is that each node's **lookup
coordinate** is shifted down by the local sag before `ds.GetData`, so the flat through-thickness
column is propagated along the curved surface (axial delta-z shift). `SAG_MODE = flat` reproduces
today's behavior exactly.

## Behavior
For a node at `(x, y, z)` in the mirror-local frame, with local surface sag `s(x,y)`:
```
flat_z = z - Z_SIGN * s(x, y)      ' node's depth below the curved surface, in the flat frame
ans    = ds.GetData(x, y, flat_z)  ' flat column temperature at that depth
```
`SAG_MODE` picks how `s(x,y)` is computed:
- **0 = flat:** `s = 0` (current behavior).
- **1 = conic:** `s = conic(r)` from `ROC`, `K` (sphere/parabola/ellipse/hyperbola; sign of ROC
  = convex vs concave).
- **2 = geometry:** `s` read from a modeled optical `Surface` by projecting the node onto it
  (any shape — asphere/measured/freeform).

**Only z shifts.** The node's own x and y are passed to `GetData` unchanged; only the z
coordinate moves (down the optical axis) by `Z_SIGN * s(x,y)`. Nothing moves sideways — the
through-thickness column just slides straight down to sit under the curved surface.
>> does this all actually make sense? x,y should not be shifted right? it should only be z or am i misreading whats happening?

## User settings (constants at top)
```vb
Const SAG_MODE As Long   = 1       ' 0 = flat, 1 = conic (ROC/K), 2 = geometry (Surface ID)
Const Z_SIGN   As Double = -1.0    ' depth direction into the substrate (confirm on first run)
' conic mode:
Const MIRROR_ROC As Double = 0.0   ' vertex radius of curvature; sign sets convex/concave; 0 = flat
Const MIRROR_K   As Double = 0.0   ' conic constant: 0 sphere, -1 parabola, <-1 hyperbola, >0 oblate
Const SLOPE_WARN_DEG As Double = 7.0  ' warn if rim surface tilt exceeds this (fast mirror -> axial-depth error)
```
(ROC/K and the optical Surface ID can also be prompted at run time; see Step 2.)

## Conic sag (lift from import_point_cloud.bas)
```
c   = 1 / ROC                       ' ROC = 0 -> treat as flat, skip (sag = 0)
r2  = x*x + y*y
arg = 1 - (1 + K) * c*c * r2
if arg < 0 then arg = 0             ' guard beyond the conic's valid aperture
sag = c * r2 / (1 + sqrt(arg))
```
(WinWrap divide-by-zero guard: copy `ROC` into a runtime variable and only divide when nonzero,
exactly as the old macro does.)

## Geometry sag (Surface projection)
Given the optical `Surface` object `su` (by ID):
```
rc = su.XYZToParam(nodeXYZ, u, v)   ' nearest param coords on the surface
rc = su.ParamToXYZ(u, v, surfXYZ)   ' surface point
sag = surfXYZ(2)                     ' its z in the mirror-local frame
```
Projection returns the nearest surface point (≈ the point directly above for slow surfaces); a
vertical ray-intersect would be more exact for steep surfaces — note as a future refinement.

## Step-by-step changes to apply_thermal_datasurface.bas
1. **Settings block:** add `SAG_MODE`, `Z_SIGN`, `MIRROR_ROC`, `MIRROR_K`.
2. **Prompts (only what the mode needs):**
   - `SAG_MODE = 1`: prompt `ROC` and `K` (default to the constants) so you can tweak per run.
   - `SAG_MODE = 2`: prompt the optical `Surface` ID; validate it exists (`su.Get`).
   - `SAG_MODE = 0`: nothing extra.
3. **A `SagAt(x, y)` helper** returning the local sag per the mode (0 / conic / projection).
4. **In the per-node loop:** replace `Coord(2) = nd.z` with
   `Coord(2) = nd.z - Z_SIGN * SagAt(nd.x, nd.y)`; everything else (Evaluate/GetData/PutArray)
   is unchanged. Track the sag range for the report.
5. **Report:** add `SAG_MODE`, ROC/K or Surface ID, and the sag min/max applied, alongside the
   existing summary.

## Trade-offs / notes
- Flat cloud is reused as-is; no Python change, no cloud regen; switch surfaces by changing
  `SAG_MODE`/ROC/K (or Surface ID).
- Axial (z) depth, not surface-normal depth — your chosen interim rule. "Slow" vs "fast" =
  the mirror's focal ratio (f/#): slow = gently curved (large f/#), fast = steeply curved
  (small f/#). Axial and normal depth are nearly identical on slow surfaces and diverge a
  little on fast ones — a known approximation of this interim method.
- `SAG_MODE = 0` is an exact regression to the current, verified behavior.

## Error checking (expanded, per request) + verbose header/comments
The macro keeps the fail-loud style (clear MsgBox, nothing applied on a hard error) and adds a
verbose header block explaining the sag math, the axial-depth assumption, the sign convention,
and each mode. New checks:
- **Mode:** `SAG_MODE` must be 0/1/2; anything else is an error.
- **Conic:** `ROC = 0` is treated as flat (no divide). Detect when the aperture exceeds the
  surface's valid radius — i.e. `(1+K)*c^2*r^2 > 1` (sqrt arg < 0) at any node — and **warn**
  with the offending radius (the conic is undefined past that; sag is clamped there).
- **Geometry:** validate the `Surface` ID exists (`su.Get`); check `XYZToParam`/`ParamToXYZ`
  return `FE_OK` per node; **count projection failures** and abort with a message if none
  succeed, warn if some did.
- **Shift range:** after shifting, count nodes whose `flat_z` falls **outside** the flat
  column's `[0, thickness]` (e.g. a node above the surface or past the back face). Nearest
  neighbor will clamp to the end value; report how many and the min/max `flat_z` so a bad sign
  or wrong CSys is obvious.
- **Sanity:** sag and every applied temperature must be finite (no NaN/inf) — error if not.
- **Fast-mirror / approximation-validity warning:** the axial-depth rule degrades as the
  surface steepens. Compute the surface tilt `theta` at the outer radius (conic:
  `theta = atan(c*rmax / sqrt(1-(1+K)*c^2*rmax^2))`; geometry: angle of `NormalAtXYZ` from the
  z-axis at the rim) and the implied depth error `~= 1 - cos(theta)`. If `theta` exceeds a
  tunable `SLOPE_WARN_DEG` (default ~7deg, about f/2, <1% error), **warn** (non-fatal) with the
  angle, the estimated error %, and an approximate f/# so you can judge if it's acceptable.

## First-run confirms (one-node checks)
- **Sign:** pick a node off-axis, verify the applied temperature corresponds to the intended
  depth below the curved surface (flips with `Z_SIGN` if backwards).
- **Conic:** spot-check `sag(r)` at the rim against a hand calc for your ROC/K.
- **Geometry:** confirm `XYZToParam`/`ParamToXYZ` return a sensible surface z at a test (x,y).

## Checklist
- [x] Settings: `SAG_MODE`, `Z_SIGN`, `MIRROR_ROC`, `MIRROR_K`
- [x] Mode-specific prompts (ROC/K; or Surface ID + validate)
- [x] `SagAt(x,y)` helper (flat / conic / geometry)
- [x] Shift the lookup z by `Z_SIGN*sag` in the node loop
- [x] Report sag mode + params + sag range
- [x] Verbose header (sag math, axial-depth assumption, sign, modes) + inline comments
- [x] Error checking: mode valid, conic aperture, geometry projection, flat_z out-of-range, finite
- [x] Fast-mirror warning: rim tilt vs `SLOPE_WARN_DEG` + estimated depth error %
- [x] `SAG_MODE=0` regression unchanged
- [x] First-run sign / conic / geometry checks

---
**Plan ready for review.** Annotate with `>>` or say "go" to implement.
>>> as always include verbose commenting and header explanation. and I'd like to see some more error checking if possible