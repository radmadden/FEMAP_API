# Research: FEMAP macro to visualize the x,y,z,T cloud (geometry check)

Goal: a **new standalone .bas** that reads the `x,y,z,T` cloud from `npz_to_3D.py`,
creates FEMAP geometry **Points** at those locations **in a mirror-local CSys** the user
enters, and **colors each point by temperature** so the spatial fit and the thermal
gradient are both visible. Reference: the existing `import_point_cloud.bas` (API idioms).

## 1. Input file (from `npz_to_3D.py`)
- Name: `<Mirror>_MirrorCloud3D_<t>s.csv`; columns **`x, y, z, T`**, one point per row.
- Header row `x,y,z,T` present by default (`WRITE_HEADER=True`) — non-numeric, so a numeric
  filter skips it automatically.
- Coordinates in **inches**, in the **mirror-local frame**: origin at the **vertex**,
  x/y in the mirror plane, z from the front surface into the thickness. T in model units
  (Kelvin in the sample data, ~294–327).
- Already fully mapped (centered, circular, dense) — the macro does **no** re-mapping
  (no Mode 2, no Y-fill, no clip). This is the key difference from the old macro.

## 2. FEMAP API building blocks (proven in `import_point_cloud.bas`)
- `Set App = feFemap()`, `Set pt = App.fePoint`, `Set cs = App.feCSys`.
- CSys validation: `cs.Get(id)` returns `FE_OK`; `cs.type` (0=rect, 1=cyl, 2=sph). IDs 0–2
  are built-in global; user CSys are >2. We require **rectangular** (our x,y,z are cartesian).
- Transform local→global: `rc = App.feCoordTransform(srcCSys, fromXYZ, 0, gXYZ)`.
  **Gotcha (documented):** the OUTPUT `gXYZ` must be a **Variant**, the input a `Double()`.
  If `srcCSys = 0` (global), skip the transform and assign directly.
- Create a point: set `pt.x/y/z`, `pt.color`, `pt.layer`, then `pt.Put(pt.NextEmptyID)`
  (NextEmptyID never overwrites existing geometry).
- Bulk speed: `App.feAppLock` before the loop, `App.feAppUnlock` + `App.feViewRegenerate(0)`
  after (and on every error exit).
- File read idiom: `Open fname For Input`, `Line Input`, normalize delimiters
  (tab/semicolon/space → comma), `Split`, `IsNumeric` per column, skip non-numeric lines.

## 3. Color-by-T design (new)
- `Point.color` is a **FEMAP palette index** (Long), not RGB — so T is shown as discrete
  **color bands**, not a continuous contour.
- Two passes: pass 1 reads the file and finds `Tmin/Tmax` (and counts); pass 2 maps each
  point's T to a band `b = floor((T-Tmin)/(Tmax-Tmin) * (Nbands))` (clamped) and sets
  `pt.color = BAND_COLORS(b)`.
- `BAND_COLORS` = a configurable array of palette indices forming a blue→red ramp. Exact
  indices depend on the model's palette, so it's a top-of-file constant to tweak; the macro
  prints the **T-value → color-index legend** in the summary.
- Optional: put the cloud on its own `LAYER_ID` (toggle in View > Visibility), as the old
  macro does.

## 4. Performance / practicality
- Point counts are large (M1 @ 0.25" ≈ 37k; smaller mirrors far fewer). Creating tens of
  thousands of geometry Points under `feAppLock` is workable but not instant.
- Provide a `STRIDE` knob (keep every Nth point) for a fast, light spot-check, and/or note
  that a coarser `GRID_SPACING_IN` upstream reduces the count.
- `SCALE_FAC` knob (like the old macro) in case file units ≠ model units (default 1.0).

## 5. Gotchas / notes
- `feCoordTransform` output **must** be a Variant (the one bug that broke the old version).
- Validate the CSys exists and is rectangular **before** reading the file; fail with a
  clear `MsgBox` and create nothing.
- The 4th column (T) is read for color only; it is **not** a FEMAP result — this is a
  geometry/placement check with a thermal tint, not a true results contour (that would be
  the data-surface path, out of scope here).
- Every early-exit after `feAppLock` must `feAppUnlock` first (the old macro's pattern).
>> this shouldn't e an issue anymore because we solved the csys thing in the .py script

## 6. Open decisions for the plan
- **Nbands / ramp**: how many bands (e.g. 12) and which palette indices? Default + tweakable.
- **STRIDE default**: 1 (all points) or a decimation for first-look speed?
- **Layer/naming**: single layer vs one layer per band for toggling individual temperature
  ranges.
- **Legend**: summary MsgBox lists band → T range → color index (planned yes).
>> I think we can cut the number of points down significantly so it can run fast. we just need to have enough to get a sense of the outer limits and a few rows/columns throughout