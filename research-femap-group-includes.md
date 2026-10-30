# Research: FEMAP macro to write groups as Nastran INCLUDE files + a Master

Goal: a **new standalone `.bas`** that takes a set of FEMAP groups and writes **one
Nastran bulk-data INCLUDE file per group**, plus a **Master file** containing an
`INCLUDE 'group_name.bdf'` line for each. Reference idioms: `show_point_cloud.bas`
(connect, App objects, feAppLock, file write, summary MsgBox).

## 1. The core finding: Nastran has no group-filtered write

- `feFileWriteNastran(brand, fName)` writes the **entire active model** as one Nastran
  input file, driven by the active analysis set (api.pdf p.3-276). There is **no group
  filter** on it.
- A "Portion of Model to Write" **group** option exists in the Analysis Set — but only
  for **ANSYS** (`AnsModelGroupID`) and **LS-Dyna** (`DynModelGroupID`). **Nastran has no
  equivalent.** (api.pdf, AnalysisMgr properties.)
- The `Pref_GroupIncludeFiles` / `Pref_PreserveIncludes` preferences are about **reading**
  INCLUDE files on import, not writing them.

**Conclusion:** FEMAP will not split a Nastran deck by group for us. The macro has to
**enumerate each group's entities and write the bulk-data cards itself.**

## 2. API building blocks (confirmed in api.pdf / existing .bas)

- Connect: `Set App = feFemap()`.
- Iterate groups: `Set gp = App.feGroup`, then `gp.First()` / `While gp.Next()` loop;
  `gp.ID`, `gp.title` per group. (`.First/.Next/.title` idiom used throughout the guide.)
- Collect a group's entities into a selection set:
  `Set ns = App.feSet` → `ns.AddGroup(entityType, groupID)` adds **all IDs of that type**
  from the group (api.pdf p.4-206). Then iterate `ns.First()` / `ns.Next()`.
  Entity types: `FT_NODE`, `FT_ELEM`, etc.
- Node coords: `Set nd = App.feNode`; `nd.Get(id)`; `nd.x`, `nd.y`, `nd.z` (and `nd.csys`).
- Element connectivity: `Set el = App.feElem`; `el.Get(id)`; `el.topology` (shape code),
  `el.propID`, and `el.Node(i)` for each corner (api.pdf confirms `feElem.Node(0)` access).
- File write: classic Basic `fnum = FreeFile : Open fName For Output As #fnum :
  Print #fnum, ... : Close #fnum` (same I/O family as the read loop in the existing macro).
- Speed/UI: `App.feAppLock` before the loop, `App.feAppUnlock` after (every exit path).
>> can we make a selector box that lets the user choose which groups to write?

## 3. Candidate approaches

### A. Manual bulk-data generation per group  ← recommended
For each selected group:
1. `ns.AddGroup(FT_NODE, gid)` → write a `GRID` card per node into `group_name.bdf`.
2. `ns.AddGroup(FT_ELEM, gid)` → write the element card per element (CQUAD4, CTRIA3,
   CBAR, CHEXA, …) keyed off `el.topology`.
3. Master deck gets `INCLUDE 'group_name.bdf'`.

- Pro: full control, exact 8-field (or large-field) formatting, no dependency on the
  analysis set, works offline.
- Con: we must implement a card writer **for each element topology that appears in your
  models**. GRID is trivial; elements are the work. We should build writers only for the
  element types you actually use.
>> this is what I want but each INCLUDE should only use the elements and nodes from the group. I want separate INCLUDE files that are just MAterials, then just properties, then just CSYS, there will also need to be a group of ICN's (intrface connection nodes) that take care of the shared interfaces. we should also build a check that looks for shared nodes in two or more groups and then removes them and populates a new ICN group. Likewise i want to api to extract the materials, props, and csys's from every group, likewise for loads, and constraints

### B. Whole-model write + INCLUDE text injection  ← rejected
`feFileWriteNastran` + `NasBulkStartText`/`NasBulkEndText` can inject arbitrary Text
entities (e.g. `INCLUDE` lines) into the bulk section, but it still writes the **whole
model to one file** — it does not split by group. Doesn't meet the goal.

### C. Entity-ID SET lists only  ← probably not what you want
Write each group as Nastran `SET`/list definitions (IDs only), not real cards. Tiny and
easy, but it's a reference list, not a partitioned mesh. Flag in case that's actually the
intent.

## 4. The decisions that shape the design (need your call)

1. **What goes in each include?** Just mesh (`GRID` + element connectivity)? Or also the
   **properties, materials, coord systems** those elements reference? Common pattern:
   mesh per group in the includes, and shared properties/materials/CSys in the **Master**
   (or in one common include) so they're defined exactly once. 
   >>see above
2. **Shared/boundary entities (the big gotcha).** A node (or element) can belong to more
   than one group. If every group writes its own `GRID`, a shared node is written twice →
   **duplicate GRID = Nastran fatal.** Options: 
   - (a) one shared `nodes.bdf` include with every GRID once, elements split per group;
   - (b) de-dupe: each entity written in the **first** group that owns it;
   - (c) assume your groups are disjoint and just warn on overlap.
   Which matches how your groups are built? 
   >> see above
3. **Which groups?** All groups in the model, a **subset you pick** (ID list / title
   prefix), or just the **active** group? >> subset like i said above
4. **Element types** actually present in your models (so I build the right card writers). all in group
5. **Field format:** small 8-char (default, simplest) vs large field. 
>> small
6. **Output location + names:** target folder, Master filename, include filename rule
   (from `gp.title`, sanitized), and **relative vs absolute** INCLUDE paths. 
   >> ask for folder and path
7. **Master contents:** INCLUDE lines only, or also a skeleton (CEND/BEGIN BULK/ENDDATA,
   shared props/materials)? 
   >> just needs includes, the bulk will be pulled into femap before running

## 5. Gotchas / notes
- Duplicate GRID/element IDs across includes are a hard Nastran error — decision #2 is
  the one that makes or breaks a solver-ready deck.
- Elements reference property IDs that must be defined somewhere the solver sees (Master
  or a common include) or the deck won't solve — ties to decision #1.
- Sanitize `gp.title` for filenames (spaces, slashes, etc.).
- `feAppLock`/`feAppUnlock` around the whole write; unlock on every early exit.
- Large models: writing is sequential file I/O, fast; no per-entity redraw concern.

---

# ADDENDUM (after your `>>` review) — expanded scope

Your notes change this from "mesh per group" into a model-partitioner. Restating what I
now understand you want, then the one architecture decision it forces, then what I still
need from you.

## 6. Revised scope — the full set of output files
For a **user-selected subset of groups**, produce:
- **One mesh INCLUDE per group** — only the `GRID`s and elements **belonging to that
  group** (group-local), nothing else.
- **`materials.bdf`** — every material referenced by the selected groups (`MAT1/2/8/9…`).
- **`properties.bdf`** — every property referenced (`PSHELL/PSOLID/PBAR/PBEAM/PCOMP…`).
- **`csys.bdf`** — every coordinate system referenced (`CORD2R/C/S`, `CORD1*`).
- **`loads.bdf`** — loads associated with the selected groups.
- **`constraints.bdf`** — constraints associated with the selected groups.
- **`icn.bdf`** — the **Interface Connection Nodes**: nodes shared by two or more selected
  groups. Written **once** here so they aren't duplicated in the per-group meshes.
- **Master** — nothing but `INCLUDE` lines for all of the above (you pull the bulk into
  FEMAP before running, so no CEND/BEGIN BULK/ENDDATA skeleton).

## 7. Shared-node → ICN logic (your core new requirement)
- Build a node→groupcount map across the selected groups (`feSet.AddGroup(FT_NODE, gid)`
  per group, union/intersect).
- Any node in **2+ groups** → the **ICN set**: write it once in `icn.bdf`, and **omit it
  from each group's mesh include**. Each group's elements still *reference* those node IDs;
  the GRID is defined once in `icn.bdf`, so no duplicate-GRID fatal.
- Create a new FEMAP group **"ICN"** populated with those nodes.
  - **OPEN Q (a):** should the shared nodes also be **removed from the original groups in
    your model** (modifies your model), or only excluded from the per-group *files* while
    your groups stay untouched? (I lean: leave your model groups alone, create ICN as a new
    group, and handle the exclusion only in the file write. Confirm.)

## 8. Confirmed API for the new scope
- **Node CSys refs:** `nd.defCSys`, `nd.outCSys` (confirmed in guide examples).
- **Element/property material:** elements and properties carry `matlID`; property carries
  its material; collect referenced IDs from there.
- **Per-type group membership:** `feSet.AddGroup(FT_PROP/FT_MATL/FT_CSYS/…, gid)` pulls
  whatever a group explicitly lists; mesh-derived collection (below) is the robust path.
- **Group picker:** FEMAP has **no** built-in multi-group dialog. The "selector box" = a
  **WinWrap custom dialog with a multi-select ListBox** filled from `gp.title` (iterate
  `gp.First/Next`). Clean and self-contained.
- **New ICN group:** `feGroup` + `.Put` + add node IDs to its node rangelist.

## 9. THE architecture decision this forces (needs your call)
Hand-mesh was fine when it was just `GRID` + elements. But now that materials, properties,
CSys, loads, **and** constraints are in scope, **hand-writing a correct card for every
MAT/P-prop/CORD/FORCE/PLOAD/SPC/MPC variant is effectively reimplementing FEMAP's whole
Nastran translator in Basic** — large and brittle. Two ways:

- **A1 — Hand-write every card type.** Full control, zero dependency, but I must build and
  maintain writers for every element/property/material/load/constraint card you use, and
  anything unanticipated silently drops.
- **A2 — Let FEMAP write once, then split & route (recommended).** Call
  `feFileWriteNastran` to a temp `.bdf` (FEMAP formats *every* card correctly), then the
  macro **parses that deck and routes each card to the right include**: `GRID`/element →
  the owning group's file (or `icn.bdf` for shared GRIDs), `MAT*`→materials, `P*`→
  properties, `CORD*`→csys, `FORCE/MOMENT/PLOAD*/GRAV/…`→loads, `SPC*/MPC*/RBE*?`→
  constraints. We only do ID-aware text routing, not card formatting — robust to element
  types we didn't anticipate.
  - Cost: parsing small-field Nastran with continuations, and a card→group lookup by ID.
  - **OPEN Q (b):** OK to go A2? It's much safer given the breadth you want.

## 10. Remaining open questions
- **(c) Loads/constraints scope:** "extract from every group" = loads/constraints
  **applied to the group's nodes/elements**, or whatever the group **explicitly contains**?
  And from **which** load set / BC set — the active one, or all? (Loads/constraints are
  organized by set, each referencing nodes/elements — this is the fuzziest part.)
- **(d) Element overlap:** you said shared *nodes* → ICN. What if an **element** is in two
  selected groups? Writing it twice = duplicate-element fatal. Warn and write once (first
  owner)? Or assume elements are disjoint?
- **(e) Property/material/CSys collection:** derive from the mesh (element `propID`/
  `matlID`, node `defCSys/outCSys`) — robust — vs. only what each group explicitly lists?
  (I lean derive-from-mesh so nothing referenced is ever missing.)

