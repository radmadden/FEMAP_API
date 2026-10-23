# Research: npz_to_3D.py — T mapping + Y-dimension build

Goal: fix `npz_to_3D.py` so it (1) correctly maps npz temperatures onto the node
map and (2) fills in real Y coordinates, producing an x,y,z,T point cloud for a
3D FEMAP import. Files reviewed: `npz_to_3D.py`, `model1_node_map.csv`,
`model1_results.npz`.

## 1. Data structures

### `model1_node_map.csv` (550 rows)
Columns: `node_id, node_name, plate, i_x, j_z, x_m, z_m, dx_m, dz_m, material,
C_J_per_K, is_surface`.

- Each row is a **cell centroid**: center `(x_m, z_m)` and cell size `(dx_m, dz_m)`, in meters.
- 11 plates / components: `M7, M6, M5, M4, M3, M1, P1, M2, W1, Mel, Maz` — each a
  5×10 grid (`i_x` 0..4 × `j_z` 0..9) = 50 nodes. 550 total.
- **x = in-plane direction**; **z = through-thickness** (spacing grows geometrically —
  boundary-layer refinement, from ~0 to the plate thickness).
- **Plate geometry is NOT uniform.** x cell size differs by plate:
  - `M7`: dx = 1.245 in → x grid spans 0 .. **6.225 in**
  - `M1`: dx = 2.795 in → x grid spans 0 .. **13.975 in**; z thickness ≈ **1.735 in**
  So any fixed x/y extent is only valid for one plate.

### `model1_results.npz`
- `T` — shape **(773, 550)** = timesteps × nodes (°, scalar field).
- `t` — shape (773,), seconds, 0 .. ~38106.
- `node_names` — shape (550,), aligned 1:1 with T's columns.
- Also present: `T_face, h_surface, q_conv, surf_plate` (55 surface entries),
  `T_air_stations` (12), `E_in/E_stored/E_air`. Not used by the script.

### Mapping check (important)
CSV `node_name` order is **identical** to npz `node_names` order, and matches
`node_id` 0..549. So `T[idx, node_id]` is correct *for this file* — but it relies on
ordering never changing. Mapping by `node_name → npz column index` gives the same
result and is robust. **Recommend switching to name-based lookup.**

## 2. What the current script does

1. Dialogs: pick node map, pick npz, enter target time, enter plate, pick output folder.
2. `timestep_idx = argmin(|t - target_time|)`, `T_snapshot = T[idx, :]`.
3. Per node in the chosen plate: `x_in = x_m*39.37`, **`y_in = x_in`**, `z_in = z_m*39.37`,
   `temp = T_snapshot[node_id]`.
4. Per x-column: prepends a z=0 surface point and appends a cumulative-thickness
   z-edge point (converts centers → full-thickness span 0..~1.735 in).
5. Adds x "slices": leading edge at x=0, thermal end at x=max+dx/2, and a **hardcoded
   CAD extension at x=15.75 in** — each forcing `Y = X`.
6. Writes CSV columns `X,Y,Z,Temperature`.

## 3. Problems found

1. **Y is wrong.** `y_in = x_in` puts every node on the diagonal plane y=x — a tilted
   sheet, not a 3D volume. This is the main thing to fix.
2. **No duplication in Y.** A spatially-y-uniform volume needs the xz grid replicated at
   (at least) two y planes: `y = 0` and `y = y_f`.
3. **Hardcoded extents (13.975, 15.75 in)** are M1-specific. Other plates have different
   x spans, so the x-envelope extension and a fixed y_f would be geometrically wrong for them.
4. **T lookup by `node_id`** is correct here but fragile; should key on `node_name`.

## 4. Target output (my understanding of the request)

Four columns `x, y, z, T`. Take the plate's xz grid (with T mapped) and emit it at
`y = 0` and again at `y = y_f`, where **y_f = x-extent** (square xy face, y uniform).
Both y-planes carry identical T (model is uniform in y).

## 5. Decisions (resolved with user)

- **A. Square side = per-plate PHYSICAL length**, measured by the user, supplied via an
  editable table. x extent and y_f both equal this length (square xy face). Known so far:
  M1=15.75, M2=3.46, M3=4.65 in; M4–M7 equal but TBD; P1/W1/Mel/Maz TBD → placeholders.
- **A-note.** Physical length is independent of the thermal-mesh x-span and can be smaller
  (M2 phys 3.46 < mesh 6.225) or larger (M1 phys 15.75 > mesh 13.975). So x node positions
  are **rescaled** to span 0..L_phys, not padded. (Flagged for confirmation in the plan.)
- **B. Keep edge treatment:** z surface(0)/full-thickness edge points per column retained;
  x boundary points at 0 and L_phys retained (now via rescale, not a fixed envelope).
- **C. Multiple y layers** between y=0 and y=L_phys (not just two), identical T per layer.
- **D.** T mapped by **node_name** (robust), verbose comments + upfront math explanation.

## 6. Per-plate reference (x uniform in dx for all plates)

| plate | dx (in) | mesh x-span (in) | phys length (in) |
|-------|---------|------------------|------------------|
| M1 | 2.795 | 13.975 | 15.75 |
| M2 | 1.245 | 6.225 | 3.46 |
| M3 | 1.245 | 6.225 | 4.65 |
| M4–M7 | 1.245 | 6.225 | TBD (placeholder) |
| W1 | 1.725 | 8.626 | TBD |
| P1 | 5.906 | 29.528 | TBD |
| Mel/Maz | 4.313 | 21.564 | TBD |

z (through-thickness) full span for M1 ≈ 1.735 in; geometric spacing, per-plate.
