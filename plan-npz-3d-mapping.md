# Plan (v2): rewrite `npz_to_3D.py` → vertex-centered circular 3D (x,y,z,T) cloud

Scope: keep the tkinter pick/prompt workflow, fix temperature mapping, and build a dense
3D point cloud of the **circular** mirror face centered on its **vertex**, from the 2D
xz thermal field. Output a 4-column `x,y,z,T` CSV in inches, per mirror, per timestep.

## Resolved with user (v2)
- Mirrors are **circular**; origin = **mirror vertex** = center of the front face = (0,0,0).
- Temperature is **directional**: `T = f(x)` across the face, **uniform in y**. (Not radial.)
- Per-plate length `L` = **diameter**; radius `R = L/2`.
- Output a **dense, interpolated** grid (not just the sparse 5x10 nodes) so FEMAP has no
  large gaps to bridge.
- Mirrors only: **M1, M2, M3, M4, M5, M6, M7**. W1/P1/Mel/Maz are **not mirrors** →
  reject with a clear message if selected.
- Rescale (not pad) is kept; the M2 thermal-vs-CAD size mismatch is a **data discrepancy
  you'll resolve upstream** — the script prints the mesh-span vs L so it's visible.

## The math / logic (goes at the top of the script as a comment block)

1. **Thermal field.** The npz gives `T[timestep, node]`. Each node (from the node map) is a
   cell centroid `(x_m, z_m)` with size `(dx_m, dz_m)`. x = in-plane, z = through-thickness
   (geometric spacing, dense near the surface). The field varies with x and z, **uniform in y**.

2. **Temperature mapping (by name).** Build `name_to_col = {node_name: col}` from the npz
   `node_names`; `temp = T[timestep_idx, name_to_col[node_name]]`. (Order matches `node_id`
   today, but name lookup stays correct regardless.)

3. **Vertex-centered circular geometry.**
   - `R = L / 2`.
   - Mesh x spans `[0, X_mesh]`, `X_mesh = ncols * dx` (centers at dx/2, 3dx/2, …).
   - Rescale + center so the vertex is x=0: `x_c = (x_m / X_mesh) * L - R`.
     Node centers land in `[-0.4L, +0.4L]` for ncols=5; the rim is `±R`.
   - Because T is uniform in y, the face is a disk filled by sweeping y: a grid point
     `(x, y)` with `x^2 + y^2 <= R^2` takes `T` from the x-profile at that x (any y).
   - z: 0 at the front surface (vertex plane), increasing into the thickness.

4. **Dense resampling.** Only 5 x-nodes and 10 z-nodes exist, so:
   - Build the in-plane grid: `x, y in [-R, R]` at spacing `GRID_SPACING_IN`, keep the disk.
   - For each z-level, `T(x)` = **linear interpolation** across the (centered) x-node temps,
     held flat beyond the outermost nodes out to the rim (edge-temperature extension).
   - z-levels = the thermal z-nodes (in inches) plus a z=0 surface point and a
     z=thickness back point (nearest-node temp). (Optional z densification left off for now.)

## Per-plate diameter table (inches) — mirrors only

```python
# Physical DIAMETER of each mirror, in INCHES. radius R = L/2.
# x and y both span [-R, R]; the face is clipped to the circle of radius R.
MIRROR_DIAMETER_IN = {
    "M1": 15.75,
    "M2": 3.46,   # NOTE: smaller than its thermal mesh span (6.225) -> see discrepancy note
    "M3": 4.65,
    "M4": 5.905,  # M4-M7 share one value (provisional)
    "M5": 5.905,
    "M6": 5.905,
    "M7": 5.905,
}
# W1, P1, Mel, Maz are NOT mirrors -> not supported; reject if selected.
```

## Config knobs (top of file)

```python
METERS_TO_INCHES = 39.37007874
GRID_SPACING_IN  = 0.25   # in-plane point spacing of the dense cloud (smaller = denser)
ADD_Z_EDGE_POINTS = True  # add z=0 surface and z=thickness back-face points
WRITE_HEADER = True       # x,y,z,T header row; flip to False if your FEMAP import wants none
# T is uniform in y, so y is just swept across the disk at GRID_SPACING_IN.
```

## Step-by-step changes to `npz_to_3D.py`

1. **Header comment block** = the math/logic above.
2. **Config + `MIRROR_DIAMETER_IN`** table near the top.
3. **Keep** the tkinter dialogs (node map, npz, target time, plate, output folder).
4. **Reject non-mirrors**: if `target_component not in MIRROR_DIAMETER_IN`, show an error
   messagebox ("`<name>` is not a mirror / not supported") and exit.
5. **T mapping by name** — build `name_to_col` from npz `node_names`.
6. **Per-plate setup**: `L = MIRROR_DIAMETER_IN[plate]`, `R = L/2`; compute `X_mesh`,
   centered node x `x_c`; print mesh-span vs L (discrepancy visibility).
7. **Build T(x,z) table**: 2D over the 5 centered x positions x the z-levels (inches),
   from the mapped temps. Add z=0 and z=thickness rows (copy nearest z row).
8. **Dense in-plane grid**: `xs = arange(-R, R+eps, GRID_SPACING_IN)` (same for ys); keep
   `(x,y)` with `x^2+y^2 <= R^2`.
9. **Assemble cloud**: for each z-level and each kept `(x,y)`: `T = interp_x(x)` at that
   z-level (flat-held past the end nodes). Append `(x, y, z, T)`.
10. **Write CSV** `x,y,z,T`, `float_format="%.8f"`,
    filename `f"{plate}_MirrorCloud3D_{int(actual_time)}s.csv"`.
11. **Remove** all old Section 4/5 logic (y=x diagonal, 13.975/15.75 hardcodes, square slices).
12. Keep success prints + messagebox; report point count, `L`, `R`, mesh-span, grid spacing.

## Trade-offs / notes
- **Point count** ~ `(pi*R^2 / s^2) * n_zlevels`. M1 (R=7.875, s=0.25) ~ 37k rows; smaller
  mirrors far fewer. If files get too big, raise `GRID_SPACING_IN`.
- **Rescale discrepancy**: M2's L (3.46) < mesh span (6.225). Script prints both and proceeds
  by rescaling; resolve the underlying thermal-vs-CAD mismatch in your source data.
- **Directional, not radial**: if a mirror is ever actually axisymmetric, this is wrong for it
  and we'd switch to a revolve. Not the case per your answer.
- **numpy/pandas** aren't on the PATH python in this session; I'll sanity-check the geometry
  math with a stdlib-only harness on the CSV (point count, origin centering, circular clip,
  interpolation), and the full np.load path runs in your Python.
>> i do have this on the machine i run it on

## FEMAP target + validation / error checking (per your note)

The deliverable feeds FEMAP's **arbitrary 3D scalar map** (an `x,y,z,T` table). The macro is
ignored. The whole run is wrapped so a bad input fails loudly with a messagebox and **no file
is written** — never a silent/partial/garbage CSV. Checks, in order:

**Inputs**
- Node map has the required columns (`node_id,node_name,plate,i_x,j_z,x_m,z_m,dx_m,dz_m`);
  clear error naming any missing.
- npz has `t`, `T`, `node_names`; `T.shape == (len(t), len(node_names))`; else error.
- Selected plate is a mirror (in `MIRROR_DIAMETER_IN`); non-mirrors rejected by name.
- Every selected-plate `node_name` exists in `name_to_col`; report the first few missing.
- x spacing is uniform for the plate (rescale assumes it); error if `dx` varies.
- `target_time` within `t` range; report the resolved timestep and actual time.
- `L > 0`, `R > 0`, `0 < GRID_SPACING_IN < R`; error otherwise (prevents an empty disk).

**Field sanity**
- No `NaN`/`inf` in the mapped temps (FEMAP's mapper chokes on them); error if found.
- Print `T` min/max for the snapshot so an obviously wrong unit/time is caught by eye.

**Output cloud**
- Non-empty; every value finite; exactly 4 numeric columns per row.
- All points inside the disk (`x^2+y^2 <= R^2+eps`) and `z` within `[0, thickness]`.
- Origin centered: `min x ≈ -max x`, `min y ≈ -max y` (vertex at 0,0).
- **De-duplicate** coincident `(x,y,z)` rows (a scalar map dislikes duplicate points).
- Warn (don't fail) if point count is huge (> ~1e6) so you can coarsen `GRID_SPACING_IN`.

**Report (messagebox + console)** — plate, L, R, mesh-span vs L (discrepancy flag),
timestep/time, grid spacing, point count, bounding box, `T` min/max, output path.

## Checklist
- [x] Header math/logic comment block
- [x] Config knobs + `MIRROR_DIAMETER_IN` table (M1-M7)
- [x] Reject non-mirror plates with clear message
- [x] Temperature mapping by `node_name`
- [x] Vertex-centered x: `x_c = (x_m/X_mesh)*L - R`; R = L/2
- [x] Build T(x,z) table + z surface/back boundary rows
- [x] Dense in-plane grid at `GRID_SPACING_IN`, clipped to disk (x^2+y^2<=R^2)
- [x] Linear T(x) interpolation, flat-held to the rim
- [x] 4-column `x,y,z,T` CSV + new filename
- [x] Remove old y=x / hardcoded-extent / square-slice logic
- [x] `WRITE_HEADER` knob for the x,y,z,T table
- [x] Input validation (columns, npz keys/shape, mirror, names, uniform dx, time, L/R/spacing)
- [x] Field + output validation (no NaN/inf, 4 cols, inside disk, centered, dedupe, size warn)
- [x] Fail-loud wrapper: messagebox + no file written on error
- [x] Summary report (messagebox + console)
- [x] Stdlib sanity-check of geometry + report

---
**Plan v2 ready for review.** Annotate with `>>` or say "go" to implement.
>>Add error checking in here too i want to make sure that we don't waste the time doing this and end up with something that doesn't work for me in femap