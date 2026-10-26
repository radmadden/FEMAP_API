# Research: auto-apply the thermal cloud via a FEMAP Data Surface (API)

Goal: a macro that builds the Arbitrary-3D data surface from the `x,y,z,T` cloud with the
settings you validated (vertex CSys, % locations = 0, min locations = 1 = nearest neighbor),
makes a load set, and applies the temperatures to the mirror nodes. Verified against
`api.pdf` (FEMAP API Guide 2025). **Everything needed is scriptable** — including the IDW
options, which was the one open risk.

## Key API findings (all confirmed in api.pdf)

### Data surface object — `Set ds = App.feDataSurf`
Properties (exact names):
- `ds.title` — name.
- `ds.csys` (INT4) — "Coordinate System in which the Data Surface should be evaluated in."
  → set to the **mirror vertex CSys**.
- `ds.vector` (BOOL) — False for a scalar (temperature).
- `ds.InterpPercent` (REAL8) — "(Arbitrary 3D) **Percent of variation locations to include**
  in interpolation." → set **0**.
- `ds.InterpMin` (INT4) — "(Arbitrary 3D) **Minimum number of variation locations to include**
  in interpolation." → set **1** (nearest neighbor).

Create it from scattered points:
- `rc = ds.VarArbitrary3DCoord(numEntries, xyz, val_xyz)` — "creates an Arbitrary 3D Data
  Surface." `xyz` = 3 reals per location; `val_xyz` = 3 per location, scalar in the first
  slot and 0,0 for the rest (scalar surface).
- `rc = ds.PutDataSurf(App.Info_NextID(FT_DATA_SURF))` — stores it.
- (Doc quirk: VarArbitrary3DCoord's "valid properties" note omits InterpPercent/InterpMin, but
  the property table tags both as "(Arbitrary 3D)". Set them before PutDataSurf and confirm in
  the Data Surface Editor on first run.)

Evaluate it anywhere:
- `rc = ds.Evaluate(x, y, z, False)` then `ds.GetData(...)` — "Before calling GetData the Data
  Surface must first have been evaluated with Evaluate()." Lets the macro read the interpolated
  temperature at any coordinate.

### Load set — `Set ls = App.feLoadSet`
- `ls.title = ...`; `ls.Put(App.Info_NextID(FT_LOAD_DIR))`; make it active (new LoadMesh/
  LoadNTemp loads reference the **active** load set).

### Nodal temperatures — `Set nt = App.feLoadNTemp`
- `nt.PutAllArray(count, ExpandedGeomLoad, NodeID[], Layer[], Color[], LoadDefinitionID[],
  Temperature[], FunctionID[])` — creates many nodal temperatures in one call, with explicit
  `Temperature[]` values. Fast bulk apply.
- Load type constant for nodal temperature: `FLT_NTEMPERATURE` (also `FT_NTHERM_LOAD` for the
  entity family).

### Loads can also *reference* a data surface
- LoadMesh/LoadGeom objects expose `DataSurface[0..2]` — "The IDs of Data Surfaces referenced
  by the various load values" — so a load's value can be driven live by a surface rather than a
  stored number. (Exact property combo to bind a nodal-temp load to a surface is the one detail
  to prototype — see Route 1.)

## Two native routes (both use FEMAP's own IDW engine, both honor csys / %=0 / min=1)

**Route A — evaluate per node, write stored nodal temps  (recommended).**
1. Build the Arbitrary-3D surface with `csys`, `InterpPercent=0`, `InterpMin=1`.
2. Create + activate a load set.
3. For each mirror node: `ds.Evaluate(node.x, node.y, node.z, …)` → `GetData` → temperature.
4. `LoadNTemp.PutAllArray(...)` with the collected `NodeID[]` / `Temperature[]`.
- Pros: certain-to-script; load set is self-contained (plain numbers); uses FEMAP's nearest-
  neighbor IDW so results match the Data Surface Editor; fast (one bulk put).
- Cons: static — change the surface and you re-run (fine, you re-run per timestep anyway).

**Route B — loads stay linked to the data surface (matches your phrasing).**
1–2. Same surface + load set.
3. Create nodal-temperature loads on the mirror nodes with `DataSurface[0] = dsID` so FEMAP
   evaluates the surface live.
- Pros: exactly "apply the temperature as a data surface to the nodes"; stays linked.
- Cons: the precise API property combo to bind a nodal-temp load to a surface needs a short
  prototype to confirm.

## Recommendation
**Go with Route A.** It uses the identical FEMAP IDW math (so it reproduces what you tuned in
the Data Surface Editor with %=0 / min=1), it is the most certain-to-script, and it leaves a
clean, self-contained load set. We can add Route B's live-link later if you want the load to
track surface edits — but for the generate → map → solve loop, stored values per timestep is
simpler and faster.

## Inputs the macro needs (prompts)
- The `x,y,z,T` file (same reader as show_point_cloud.bas).
- The **mirror vertex CSys ID** (rectangular) — used as `ds.csys` and to interpret node coords.
- The **mirror nodes** — a group or selection (prompt for a group ID, or use the active group).
- A **load set** name/ID (auto-name by mirror + timestep; create fresh so runs don't pile up).
- Constants at top: `INTERP_PERCENT=0`, `INTERP_MIN=1` (so the nearest-neighbor choice is
  documented and tweakable).

## Details to confirm during the plan/prototype
- Coordinate frame expected by `ds.Evaluate` (global vs the surface's csys) — feed node coords
  accordingly.
- Exact `GetData` call shape for a scalar surface.
- Route B only: which property combo binds a nodal-temp load to `DataSurface[]`.
- Node-set gathering API (feSet AddGroup / rule) for the mirror nodes.
