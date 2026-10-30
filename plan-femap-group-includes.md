# Plan: `write_group_includes.bas` — groups → Nastran INCLUDE files + Master

Decisions locked from the research review:
- **A2 architecture:** FEMAP writes the full deck once (`feFileWriteNastran`), the macro
  **parses the bulk section and routes each card** to the right include. No hand-coded
  card formats.
- **Groups:** user picks a **subset** via a multi-select dialog.
- **Loads/constraints:** write **all load sets & BC sets**, filtered to cards that touch
  the selected groups' **nodes/elements**.
- **Element overlap:** none by construction (groups numbered disjoint) → no element check.
- **ICN:** you build the ICN group yourself with its own number; the macro just treats it
  as one of the selectable groups. **No auto shared-node detection, no model edits.**
- **Field format:** small (8-char). **Output:** prompt for folder + master filename.
  **Master:** INCLUDE lines only.

New file: [write_group_includes.bas](write_group_includes.bas). Nothing else changes.

---

## Output file set (all in the chosen folder)
- `<group_title>.bdf` — one per selected group: its `GRID`s + its element cards only.
- `materials.bdf` — all `MAT*` cards.
- `properties.bdf` — all property cards (`PSHELL/PSOLID/PBAR/PBEAM/PCOMP/PROD/PBUSH/…`).
- `csys.bdf` — all `CORD1*/CORD2*` cards.
- `loads.bdf` — load cards touching selected nodes/elems (+ model-global loads).
- `constraints.bdf` — `SPC/SPC1/SPCADD/MPC/MPCADD` touching selected nodes.
- `misc.bdf` — any bulk card not matched above (e.g. `PARAM`, `GRDSET`), so nothing is
  silently dropped.
- `<master>.dat` — one `INCLUDE 'name.bdf'` per file that actually got content. The
  master is a **`.dat`**; every include is a **`.bdf`**.

Empty buckets produce no file and no INCLUDE line.

---

## Step 1 — Connect + top-of-file knobs
```
Const BRAND  As Long = 8     ' feFileWriteNastran brand: 0=MSC, 8=NX  (tweak to taste)
Const MISC_FILE As String = "misc.bdf"
Dim App As femap.model : Set App = feFemap()
```

## Step 2 — Multi-select group dialog
No FEMAP group-picker exists, so use a WinWrap custom dialog with a **multi-select
ListBox** filled from the model's groups.
```
Dim gp As femap.Group : Set gp = App.feGroup
' iterate gp.First()/gp.Next(), collect gp.ID into ids(), gp.title into titles()
Begin Dialog GRPDLG 260,200, "Select groups to export"
    Text       10,5,200,10, "Groups:"
    ListBox    10,18,240,150, titles$(), .Picked, 2   ' style 2 = multi-select
    OKButton   10,175,70,14
    CancelButton 90,175,70,14
End Dialog
Dim dlg As GRPDLG
If Dialog(dlg) = 0 Then Exit Sub          ' cancelled
```
`.Picked` returns the selected indices → map back to group IDs. Abort if none picked.

## Step 3 — Prompt for output folder + master name
```
outDir = InputBox("Output folder for the include files:", "Write Group Includes", "C:\temp\includes")
masterName = InputBox("Master filename (.dat):", "Write Group Includes", "master.dat")
```
Create `outDir` if missing (`MkDir`), ensure trailing `\`. Force the master to end in
`.dat` (append if the user leaves it off); every include is written as `.bdf`.

## Step 4 — Build ownership maps (before the write)
For each selected group build its node and element ID membership, and a global union:
```
Dim ns As femap.Set, es As femap.Set
For each selected group gid (index gi):
    Set ns = App.feSet : ns.Clear : ns.AddGroup(FT_NODE, gid)
    id = ns.First() : While id>0 : nodeOwner.Add CStr(gi), CStr(id) : id = ns.Next() : Wend
    Set es = App.feSet : es.Clear : es.AddGroup(FT_ELEM, gid)
    id = es.First() : While id>0 : elemOwner.Add CStr(gi), CStr(id) : id = es.Next() : Wend
```
- `nodeOwner` / `elemOwner`: WinWrap `Collection`s keyed by `CStr(entityID)` → owning
  group index. Membership test = error-trapped `InColl(c, key)` helper.
- `selNodes` = union of all selected node IDs (for load/constraint filtering).
- Sanitize each `gp.title` → filename (`[^A-Za-z0-9_-]` → `_`; empty/dup → `group_<ID>`).

## Step 5 — Make FEMAP write the full deck (temp file)
Avoid export dialogs and force our options without touching your real analysis set:
```
Dim aMgr As femap.AnalysisMgr : Set aMgr = App.feAnalysisMgr
' create a TEMP analysis set, NX solver, so no dialog appears:
aMgr.title = "TEMP_GroupInclude" : aMgr.solver = <NX> : aMgr.Put(tempSetID)
aMgr.NasBulkLargeField = 0            ' small field
aMgr.NasBulkWriteAllLoadBCSets = True ' all load sets + BC sets to bulk
aMgr.Active(tempSetID)
App.feFileWriteNastran(BRAND, outDir & "_full_temp.bdf")
```
Delete the temp analysis set afterward so your model is left as-is. (Trade-off: if a
clean temp set still triggers a prompt on your install, fall back to saving/restoring the
two flags on your active set — flagged for the review.)

## Step 6 — Parse the bulk section and route
Open the temp `.bdf`, process **only** lines between `BEGIN BULK` and `ENDDATA`.
```
Do While Not EOF(f)
    Line Input #f, ln
    If before BEGIN BULK or after ENDDATA Then skip
    If isContinuation(ln) Then route to currentBucket, continue
    card = UCase(Trim(Left(ln,8)))        ' field 1
    id2  = Val(Mid(ln,9,8))               ' field 2 (ID for GRID/elem/prop/mat/cord)
    bucket = Classify(card, id2, ln)      ' decides target stream (see below)
    currentBucket = bucket : route(ln, bucket)
Loop
```
`isContinuation` = first 8 cols blank, or first char `+`/`*`.

### Classify()
| Card prefix | Target | Rule |
|---|---|---|
| `GRID` | group file of `nodeOwner(id2)` | owned? else → `misc` + warn |
| element cards (`C*` connectivity) | group file of `elemOwner(id2)` | disjoint, unambiguous |
| `MAT*` | `materials.bdf` | always |
| `P` + prop (`PSHELL/PSOLID/PBAR/PBEAM/PCOMP/PROD/PBUSH/PELAS/…`) | `properties.bdf` | always |
| `CORD1*`,`CORD2*` | `csys.bdf` | always |
| node loads (`FORCE/MOMENT/SPCD/TEMP*`) | `loads.bdf` | keep if grid field ∈ `selNodes` |
| elem loads (`PLOAD2/PLOAD4/…`) | `loads.bdf` | keep if elem field ∈ selected elems |
| global loads (`GRAV/RFORCE/ACCEL*/…`) | `loads.bdf` | always keep |
| constraints (`SPC/SPC1/SPCADD/MPC/MPCADD`) | `constraints.bdf` | keep if any node ∈ `selNodes` |
| anything else (`PARAM/GRDSET/…`) | `misc.bdf` | + warn once per card type |

- A classified-out load/constraint (no selected node/elem) is **dropped** (that's the
  "by node/elem" filter you asked for).
- Element-card detection: treat field-1 starting with `C` and not in the load list as an
  element; its EID is field 2. (The exact `C*` load exclusions — `CORD*` handled above —
  are listed explicitly so no element is misread.)
- **Open item for review:** the per-card "which field is the grid/elem" map for the load
  list. I'll implement the common cards above; if a load card isn't in the map, default to
  **keep** (never silently drop). Confirm that default.

### route()
Lazily open each target file on first write (so empty buckets make no file). Keep file
numbers in arrays: `grpFnum(gi)`, plus fixed handles for the six shared buckets. Write the
line verbatim — FEMAP already formatted it.

## Step 7 — Master + cleanup
- Close every opened stream.
- Write `<master>.dat`: an `INCLUDE 'name.bdf'` line for each `.bdf` that got content
  (bare filenames, quoted; all live in the same folder).
- Delete `_full_temp.bdf` and the temp analysis set.

## Step 8 — Summary MsgBox
Groups exported + per-file line counts, plus warnings: GRIDs with no selected owner,
loads/constraints dropped by the filter, and any `misc.bdf` card types.

---

## Trade-offs / risks
- **A2 depends on the temp write not prompting.** Mitigated by a clean temp NX analysis
  set; documented fallback if your install still prompts.
- **Load/constraint field map** is the fuzziest piece; default-keep prevents data loss,
  but may over-include an occasional global card. Easy to tighten after a test run.
- **GRID ownership** assumes every node is in exactly one selected group (your ICN
  discipline). Nodes in an unselected group surface as a warning, not a silent drop.
- Small-field only; large-field models aren't a target (you chose small).

## Checklist
- [x] Top-of-file knobs (`BRAND`, file names) + connect
- [x] Multi-select group dialog, map picks → group IDs
- [x] Prompt output folder + master `.dat` name (force `.dat`); create folder
- [x] Build `nodeOwner`/`elemOwner` + sanitized filenames + membership helper
- [x] Active set: small field + write-all-load/BC (save/restore, or temp set); write `_full_temp.bdf`
- [x] Parse BEGIN BULK…ENDDATA with continuation handling
- [x] `ProcessCard()` routing incl. load/constraint node/elem filter
- [x] `WriteToGroup`/`WriteToBuck` with lazy file open per bucket/group
- [x] Write master `.dat` INCLUDE list (only non-empty files)
- [x] Delete temp file + temp analysis set
- [x] Summary MsgBox with counts + warnings
- [ ] **Dry-run in FEMAP** on a small multi-group model; confirm no duplicate GRID (yours to run)

**Implemented in [write_group_includes.bas](write_group_includes.bas).** Couldn't run it
here (no FEMAP), so the in-FEMAP dry-run is the open item.
>> the Master should be a .dat and all the includes should be .bdf you should also have it ask for a name for the .dat