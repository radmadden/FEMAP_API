# Plan: visualize the x,y,z,T cloud in FEMAP (geometry check)

Two deliverables:
1. **Python** — add a sparse **skeleton** export to `npz_to_3D.py` (rim + a few rows/cols,
   a couple z-levels) so the check plots in a few hundred points, not ~37k.
2. **New macro** — `show_point_cloud.bas` reads an `x,y,z,T` file and places FEMAP Points in a
   rectangular **mirror-local CSys** you enter (vertex-local → global), all one color
   (the T column is ignored — this is a pure placement check).

---

## Part 1 - Python skeleton export

### Refactor (share the field model)
Pull the per-plate geometry/field out of `build_cloud` into a helper so dense and skeleton
paths share it and stay consistent:
- `_build_field_model(df, t, T, names, plate, target_time)` → returns
  `(x_nodes_c, z_levels, T_xz, R, thickness_in, info)` (all the validation + centering +
  T(x,z) table already in `build_cloud`).
- `build_cloud` = field model + the existing dense grid (unchanged output).
- `build_skeleton(field, params)` = field model + the sparse sampling below.
- Shared `_interp_T(x_query, x_nodes_c, Tcol)` (flat-held linear) used by both.

### Skeleton geometry (all in the vertex-local frame, inches)
Config block near the top:
```python
SKELETON = dict(
    ring_pts   = 72,          # points around the outer rim (every 5 deg)
    n_rows     = 5,           # horizontal chords (constant y)
    n_cols     = 5,           # vertical chords (constant x)
    pts_per_line = 25,        # samples along each chord
    z_levels   = ("surface", "back"),  # which through-thickness planes to emit
)
```
- **Rim ring**: `ring_pts` points at exactly `r = R`, angle `theta` 0..2pi;
  `x = R*cos(theta)`, `y = R*sin(theta)`; `T = _interp_T(x)` (flat-held → end-node temp).
- **Rows** (constant y): `n_rows` y-values evenly spaced in `(-R, R)`; for each, sample
  `pts_per_line` x in `[-x_lim, x_lim]`, `x_lim = sqrt(R^2 - y^2)`; `T = _interp_T(x)`.
- **Cols** (constant x): `n_cols` x-values evenly spaced in `(-R, R)`; for each, sample
  `pts_per_line` y in `[-y_lim, y_lim]`; `T = _interp_T(x)` (constant down a column → shows
  iso-T chords).
- **z_levels**: `"surface"`→0.0, `"mid"`→thickness/2, `"back"`→thickness. Emit each chosen
  plane; T uses that plane's z-row of `T_xz` (interp along x at that z).
- De-duplicate `(x,y,z)` (rows/cols cross; rim meets chord ends).

### Output + validation
- Reuse the same output checks (finite, inside disk, centered, z in range, dedupe).
- Filename `f"{plate}_VizSkeleton_{int(actual_time)}s.csv"`, columns `x,y,z,T`, `WRITE_HEADER`.
- Estimated size: `(72 + 5*25 + 5*25) * len(z_levels)` ≈ **~640 points** for 2 planes.

### Driver
- Add an output-type prompt in `main()`: `1 = dense analysis cloud`, `2 = viz skeleton`,
  `3 = both`. Default `2` for the visualization workflow.
- Skeleton path writes the `_VizSkeleton_` file; dense path unchanged.

---

## Part 2 - `show_point_cloud.bas`

### User settings (top of file)
```vb
Const LAYER_ID As Long = 9100          ' cloud layer; toggle in View > Visibility
Const PT_COLOR As Long = 12            ' single FEMAP palette index for all points
Const SCALE_FAC As Double = 1.0        ' file units -> model units (1.0 = inches->inches)
Const STRIDE As Long = 1               ' keep every Nth point (safety if a dense file is used)
```

### Flow (single pass, single color)
1. `Set App = feFemap()`, `Set pt = App.fePoint`, `Set cs = App.feCSys`.
2. Prompt **file path** (csv/txt); `Dir()` check → MsgBox + exit if missing.
3. Prompt **mirror local CSys ID** (0 = global). Validate exactly as the old macro:
   exists (`cs.Get`), and **rectangular** (`cs.type = 0`) — else MsgBox + exit, nothing created.
4. `App.feAppLock`.
5. **Read + create**: parse each line (normalize delimiters, `Split`, require **≥3** numeric
   columns; use `x,y,z` = cols 0-2, ignore any T; skip header/short/non-numeric). Every
   `STRIDE`-th valid row:
   - `fromXYZ = (x,y,z) * SCALE_FAC`; if CSys = 0 assign directly, else
     `feCoordTransform(cs, fromXYZ, 0, gXYZ)` with **Variant** `gXYZ` (the documented fix).
   - `pt.color = PT_COLOR`, `pt.layer = LAYER_ID`, `pt.Put(pt.NextEmptyID)`.
   If no valid rows → unlock + MsgBox + exit.
6. `App.feAppUnlock`, `App.feViewRegenerate(0)`.
7. **Report** (MsgBox): file, lines read, points created, skipped, CSys, layer.
8. `CSysFail:` label unlocks + regenerates + reports (old macro's pattern).

### Reuse from `import_point_cloud.bas`
- `PlacePoint` helper (CSys=0 direct vs `feCoordTransform` with Variant out).
- `feAppLock/Unlock`, `NextEmptyID`, delimiter-normalizing reader, CSys validation.

---

## Trade-offs / notes
- **Single color** — pure placement/geometry check; the T column is ignored by the macro.
  (A colored-by-value view would be the data-surface path, out of scope.)
- Skeleton keeps the check fast; `STRIDE` in the macro is a backstop if a dense file is fed.
- I can syntax-check the Python here (stdlib harness for the skeleton geometry); the `.bas`
  can't run outside FEMAP, so it gets a careful dry read, not an execution test.

## Checklist
- [x] Python: refactor `_build_field_model` + shared `_interp_T`
- [x] Python: `build_skeleton` (rim + rows + cols + z-levels) + dedupe + validation
- [x] Python: output-type prompt (dense / skeleton / both) + `_VizSkeleton_` file
- [x] Python: stdlib sanity-check of the skeleton (counts, on-rim, centered, T range)
- [x] `show_point_cloud.bas`: settings (`PT_COLOR`, `LAYER_ID`, `SCALE_FAC`, `STRIDE`)
- [x] Macro: CSys validate (exists, rectangular) before reading
- [x] Macro: single-pass read (>=3 numeric cols, ignore T) + CSys transform (Variant out)
- [x] Macro: AppLock/Unlock, NextEmptyID, single color, layer, STRIDE
- [x] Macro: summary + CSysFail unlock path

---
**Plan ready for review.** Annotate with `>>` or say "go" to implement.
