# Plan: apply_thermal_datasurface.bas  (Route A — evaluate per node, bulk-apply)

A new FEMAP macro that builds the Arbitrary-3D data surface from the `x,y,z,T` cloud with your
validated settings (vertex CSys, % locations = 0, min locations = 1 = nearest neighbor),
evaluates it at each mirror node, and bulk-applies the results as nodal temperatures in a fresh
load set. Verified against api.pdf; the guide's own `GetData` example is essentially this flow.

## Assumptions / prerequisites
- The `x,y,z,T` file from `npz_to_3D.py` (dense `MirrorCloud3D`), in the mirror-local frame.
- A **rectangular CSys at the mirror vertex** already exists in the model (you enter its ID).
- The mirror's nodes are identifiable — a **group** you name (or an interactive node select).
- Model length units match the file (inches); temperatures carry through as-is (K in the data).

## User settings (constants at top)
```vb
Const INTERP_PERCENT As Double = 0#   ' DataSurf % locations to include (0 = nearest only)
Const INTERP_MIN     As Long   = 1    ' DataSurf min locations (1 = nearest neighbor)
Const DS_TITLE_PFX   As String = "ThermalCloud"
Const LS_TITLE_PFX   As String = "ThermalMap"
Const SCALE_FAC      As Double = 1.0  ' file units -> model units
```

## Confirmed API calls (api.pdf)
- `Set ds = App.feDataSurf` — properties `title`, `csys`, `vector`, `InterpPercent`, `InterpMin`.
- `ds.VarArbitrary3DCoord(numEntries, xyz, val_xyz)` — build from scattered points
  (`xyz` 3/loc; `val_xyz` 3/loc, scalar in slot 0, `0,0` in slots 1-2).
- `ds.PutDataSurf(App.Info_NextID(FT_DATA_SURF))`.
- `ds.GetDataSurf(id)`, `ds.Evaluate(0,0,0,False)`, then per location
  `ds.GetData(0, feaID, Coord, ans, vXYZ)` → `ans` = interpolated scalar (guide example p.5-516).
- `Set ls = App.feLoadSet`; `ls.title=…`; `ls.Put(App.Info_NextID(FT_LOAD_DIR))`; make active.
- `Set nt = App.feLoadNTemp`;
  `nt.PutAllArray(count, ExpandedGeomLoad[], NodeID[], Layer[], Color[], LoadDefinitionID[],
  Temperature[], FunctionID[])` — bulk nodal temperatures. (Type family `FT_NTHERM_LOAD` /
  `FLT_NTEMPERATURE`.)
- `Set nd = App.feNode` (`nd.Get(id)`, `nd.x/y/z`); `Set es = App.feSet` for the node list.

## Step-by-step
1. **Connect + objects**: `feFemap`, `feDataSurf`, `feLoadSet`, `feLoadNTemp`, `feNode`, `feSet`.
2. **Prompts**
   - File path to the `x,y,z,T` CSV (`Dir` check).
   - Mirror **vertex CSys ID** (0 allowed) — validate it exists and is **rectangular**
     (`cs.Get`, `cs.type = 0`), else MsgBox + exit.
   - Mirror **node group ID** (or `es.Select(FT_NODE, True, "Select mirror nodes")`).
   - A tag for naming (e.g. mirror + timestep) for the surface and load-set titles.
3. **Read the cloud** (reuse show_point_cloud's delimiter-normalizing reader): require ≥4 numeric
   columns; fill `xyz(3*n-1)` and `valxyz(3*n-1)` with `T` in slot 0, `0,0` in slots 1-2;
   apply `SCALE_FAC` to coords. Bail if no valid rows.
4. **Build the data surface**
   - `ds.title = DS_TITLE_PFX & tag`; `ds.csys = vertexCSys`; `ds.vector = False`;
     `ds.InterpPercent = INTERP_PERCENT`; `ds.InterpMin = INTERP_MIN`.
   - `rc = ds.VarArbitrary3DCoord(n, xyz, valxyz)`; `dsID = App.Info_NextID(FT_DATA_SURF)`;
     `ds.PutDataSurf(dsID)`.
5. **Gather mirror nodes** into `es` (from the group, or the interactive select).
6. **Create + activate the load set** (before making loads — LoadNTemp always targets the
   active set): `ls.title = LS_TITLE_PFX & tag`; `lsID = App.Info_NextID(FT_LOAD_DIR)`;
   `ls.Put(lsID)`; set it active. *(Exact active-set call to confirm in the prototype — see below.)*
7. **Evaluate per node**: `ds.GetDataSurf(dsID)`; `ds.Evaluate(0,0,0,False)`; then
   ```
   id = es.First()
   While id > 0
       nd.Get(id)
       Coord(0)=nd.x : Coord(1)=nd.y : Coord(2)=nd.z   ' global; ds.csys handles the frame
       rc = ds.GetData(0, id, Coord, ans, vXYZ)
       NodeID(k)=id : Temp(k)=ans : k=k+1
       id = es.Next()
   Wend
   ```
   Wrap the loop in `feAppLock` / `feAppUnlock`.
8. **Bulk-apply** nodal temperatures: fill `ExpandedGeomLoad()=False`, `Layer()=1`,
   `Color()=<PT>`, `LoadDefinitionID()=0`, `Temperature()=Temp()`, `FunctionID()=0`;
   `nt.PutAllArray(k, …)`.
9. **Report** (MsgBox): file, points in surface, CSys, nodes loaded, T min/max applied,
   data-surface ID, load-set ID. Unlock on every exit path.

## Trade-offs / notes
- Uses FEMAP's own IDW engine via `Evaluate`/`GetData`, so results match the Data Surface
  Editor with %=0 / min=1 exactly — the per-node loop just reads those values.
- `GetData` feeds **global** node coords; the surface's `csys` defines its evaluation frame
  (set it to the vertex CSys, same as the cloud).
- Node-by-node `GetData` is a WinWrap loop; fine for typical mirror node counts (thousands).
  If a mirror has very many nodes and it drags, we can batch or fall back to macro-side nearest
  neighbor — but try native first.
- The data surface persists in the model (handy to re-inspect). Re-running makes a new surface
  and a new load set (named by tag) so nothing is clobbered; offer to delete old ones later.
- One timestep per load set — matches the generate → map → solve loop.

## Prototype / confirm on first run (small, low-risk)
- Exact **active-load-set** call (set `lsID` active) — one line; confirm the method name.
- `ds.GetData` output args for a **scalar** surface (`ans` scalar + dummy `vXYZ`).
- `feSet`-from-**group** call (`es.AddGroup` / rule) vs interactive `es.Select`.
- `VarArbitrary3DCoord` `val_xyz` layout for a scalar (T in slot 0) — sanity-check one value
  against a hand nearest-neighbor lookup.

## Checklist
- [x] Settings block (`INTERP_PERCENT`, `INTERP_MIN`, titles, `SCALE_FAC`)
- [x] Prompts: file, vertex CSys (validate rectangular), node group, tag
- [x] Cloud reader -> `xyz` + scalar `val_xyz`
- [x] Build Arbitrary-3D surface (csys, InterpPercent, InterpMin) + PutDataSurf
- [x] Gather mirror nodes into a Set
- [x] Create + activate load set
- [x] Evaluate + GetData per node (AppLock), collect NodeID/Temp
- [x] `LoadNTemp.PutAllArray` bulk apply
- [x] Summary report + unlock on all exits
- [x] First-run prototype of the 4 confirm items above

---
**Plan ready for review.** Annotate with `>>` or say "go" to implement.
>>I think that looks good, as always use verbose commenting and explanation of the iplementation/math/assumptions in the header 