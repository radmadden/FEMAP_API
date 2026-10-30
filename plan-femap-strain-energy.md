# Plan: sum_strain_energy.bas

Sum element strain energy over a user-selected set of elements, broken down by group and by
property, for one or more output sets. See `research-femap-strain-energy.md` for the API and
engineering basis.

Decisions locked from the research review:
- **Selection:** a plain element pick, **plus** a breakdown by group and by property in the
  same run.
- **Percent basis:** **both**. Our computed share (selected ÷ total of every element with
  output) and the sum of Nastran's `VEO_STRAIN_ENERGY_PERCENT` over the same elements.
- **Output:** Messages window only. *(Revised in Revision 2 below: the user now chooses
  the output at run time.)*
- **Extras:** none (no SED, no peak element).
- **Complex / frequency-response sets:** not supported. No special handling.

> The first version of `sum_strain_energy.bas` was written before this review and covered only
> the plain selection with a computed percent. Items marked **(NEW)** were added after the
> review.

---

## What the user supplies at run time
1. **Elements:** `feSet.Select(FT_ELEM, …)`. Required. This is "the selection".
2. **Groups for the breakdown (NEW):** `feSet.Select(FT_GROUP, False, …)`. Optional, so
   Cancel or an empty pick means no group rows.
3. **Property breakdown? (NEW):** a Yes/No MsgBox.
4. **Output sets:** `feSet.Select(FT_OUT_CASE, …)`, multi-select.

### Scope of each breakdown row (decision to confirm)
- **Group rows = the whole group**, using `Set.AddGroup(FT_ELEM, groupID)`. They're **not**
  limited to the element pick. This way you can select every element for the overall row and
  still get each group's true share. (The alternative is to limit group rows to the selected
  elements. Say if you'd rather have that.)
- **Property rows = only the selected elements**, one row for each property that appears in
  the selection. These rows add up to the selection row.

## Algorithm
1. Connect and run the prompts above.
2. **Group sets (NEW):** for each picked group, build an element set with `AddGroup` and keep
   its ID and title.
3. **Property map (NEW):** walk the model's properties (`feProp.First/Next`). For each one, do
   `ps.Clear`, then `ps.AddRule(propID, FGD_ELEM_BYPROP)`. Every element in `ps` that's also in
   the selection gets `propOf(elemID) = slot`. `propOf` is a `Long` array sized to
   `eSet.Last()`, the selection's highest ID. FEMAP's WinWrap has no `Collection`. Only properties with at
   least one selected element get a slot. This costs one pass over the model, with no
   `feElem.Get` per element.
4. Vector IDs: `seVec = Elemental(VEO_STRAIN_ENERGY)` and **(NEW)**
   `pcVec = Elemental(VEO_STRAIN_ENERGY_PERCENT)`.
5. For each output set:
   a. `rbo.Clear`, then `AddColumnV2(set, seVec)`. If that fails, warn and skip the set.
   b. **(NEW)** `AddColumnV2(set, pcVec)`. If it fails, the Nastran % shows as `n/a` for this
      set, and the run continues.
   c. `Populate` over all elements, then `GetColumn` for both columns. They share the same
      rows, so the IDs line up.
   d. One pass over the rows. Add every SE value to the total. For each "bucket" the element
      belongs to (the selection, any picked group, its property slot), add its SE, its Nastran
      %, and +1 to the element count.
   e. Print a block (below).
6. If any output sets were skipped, warn that ESE needs to be requested.

## Output (Messages window)
```
Strain energy sum: 1234 selected elements, vectors SE=<id> SE%=<id>
Set 1: <title>   Total SE = 4.567890E+01
  Bucket,             SE,            Calc %,  Nastran %,  Elems w/ data
  Selection,          1.234567E+01,  27.03%,  27.03%,     1234
  Group 3: Skin,      ...
  Group 7: Ribs,      ...
  Prop 12: 0.080 Al,  ...
  Prop 15: Frame,     ...
Set 2: ...
```
The lines are comma-separated, so they paste cleanly into Excel. `Nastran %` shows `n/a` when
the set has no percent vector.

## Helper routines (NEW)
- `PrintRow(App, label, se, total, pcSum, havePc, n)` formats one bucket line.
- `AddTo(…)` adds one element's SE, % and count into a bucket. (The property map is built
  inline in `Main`.)

## Assumptions / limits (documented in the header)
- ESE has to have been requested in the solve. If it covered only some elements, "Total" and
  "Calc %" are relative to those elements, while "Nastran %" is relative to Nastran's subcase
  total. That's exactly why both are shown.
- An element can be in several picked groups, so group rows can add up to more than 100%.
- Modal ESE magnitudes depend on the eigenvector normalization. Both percents don't.
- Rigid and mass elements carry no ESE and don't add to `Elems w/ data`.
- Complex and frequency-response output sets aren't supported.

## Status
Plan approved and implemented in `sum_strain_energy.bas`. Group selection uses
`Select(FT_GROUP, True, …)`, so it starts empty. Not yet run in FEMAP. First-run checklist:
- On a static case with `ESE=ALL`, select every element and confirm both percents are 100%
  and the total matches the .f06 `TOTAL ENERGY OF ALL ELEMENTS`.
- Turn on the property breakdown and confirm the property rows add up to the selection row.
- Pick one group and compare it with the sum of that group's `PERCENT OF TOTAL` in the .f06.
- Run against an output set without ESE and confirm it's skipped with the warning.
- On a modal run, select all the modes and confirm one block per mode.

---

## Revision 2: output choice (Messages / Excel / CSV)

After the first successful FEMAP run, the user asked for a file output that's easy to read,
chosen at run time.

Decisions locked:
- **A run-time choice of 3 outputs:** **Messages window only**, **Excel workbook**, or
  **CSV**. The Messages window block **always** prints as it does today, and the other two
  are added on top.
- **Excel layout:** a **Summary** sheet plus a **Detail** sheet.
- **Excel file:** a new workbook is **left open and unsaved**. You save it wherever you like.
- **CSV:** prompt for a path. The default is the model's folder plus
  `<model>_strain_energy.csv`.

### Selection name
Right after the element pick, an `InputBox` asks for "Name for this selection" (default
`Selection`). A blank answer or Cancel keeps `Selection`. The name replaces the
`"Selection"` bucket label everywhere: the Messages window, the Excel Summary/Detail and the
CSV. The Detail/CSV **Type** column still says `Selection`, so you can filter on it. The name
also goes in the Summary sheet's title rows ("<name>: 1234 elements").

### Prompt
After the property Yes/No, one small dialog:
```
Output:  (o) Messages window only   ( ) Excel workbook   ( ) CSV file
```
This uses a `Begin Dialog` with an `OptionGroup`, the same dialog style as
write_group_includes. The default is Messages window only.

### Collecting the results
Right now each output set is printed and then forgotten. Instead, keep the values
for every set:
- `rSE(s, b)`, `rPC(s, b)`, `rN(s, b)`: per output set `s` and bucket `b`.
- `rTot(s)`, `rHavePc(s)`, `rSetID(s)`, `rTitle(s)`.

Skipped sets (those with no ESE) are left out of the file outputs. They're still reported in
the Messages window.

### Excel workbook (`CreateObject("Excel.Application")`; api ~3942 confirms CreateObject/GetObject)
- `xl.Visible = True`, then `Workbooks.Add`. Rename the sheets to **Summary** and **Detail**
  (deleting any extra sheets).
- **Summary sheet:** the Calc % matrix. One row per bucket, one column per output set.
  - Rows 1–3 hold the title "Strain energy share (Calc %)", the model name, the selection
    count and the date.
  - The header row shows "SetID: title" for each output set, with a **Total SE** row under the
    header.
  - Values are stored as fractions with the number format `0.0%`, and get a **3-color scale**
    (white → yellow → red) so the hot spots stand out.
  - Bucket labels are in column A. The header row is bold with a fill and wraps long set
    titles. Panes are frozen at B7, under the header and the Total SE row. Column A is autofit
    (capped at 45), and the set columns are a fixed width of 14.
  - Groups and properties are visually separated: a thin rule above the first group row and
    above the first property row.
- **Detail sheet:** one long table, one row per (output set, bucket).
  - Columns: `SetID | Set Title | Bucket | Type (Selection/Group/Property) | SE | Total SE |
    Calc % | Nastran % | Elems w/ data`.
  - SE uses `0.000E+00` and the percents use `0.00%`. Nastran % is left blank when the set has
    no percent vector.
  - Header bold, an AutoFilter on, panes frozen under the header, columns autofit.
- Each sheet is filled in **one** `Range.Value = <2-D Variant array>` call, not cell by cell.
  Excel automation is slow per cell.
- If `CreateObject` fails (no Excel), print a warning and **fall back to CSV** so the run
  isn't wasted. *(Decision to confirm.)*

### CSV
- The same columns as the Detail sheet, with a header row. Values are plain numbers (percents
  as `27.03`, not `0.2703`) so the file reads naturally in a text editor or Excel. Fields that
  contain a comma (titles) are quoted.
- Default path: `feAppGetModel(m)` then `feAppGetModelName(m, name)` (api ~39723, ~39788).
  For an untitled model, fall back to `C:\temp\strain_energy.csv`. Shown in an `InputBox`, so
  it can be changed, and a blank answer means skip the CSV.

### Helpers (NEW)
- `WriteExcel(...)` and `WriteCsv(...)` take the collected arrays.
- `BucketType(b)` returns "Selection", "Group" or "Property" from the bucket index.
- `CsvQuote(s)` wraps a field in quotes when it contains a comma or a quote.

### Commenting
Write verbose comments, following the `CLAUDE.md` rule. In particular, explain the Excel COM
pieces (why one bulk `Range.Value` write, what each format and color-scale call does, what
the late-bound `CreateObject` needs), the result-collection arrays, and the CSV quoting.
While making these edits, also add more comments to the existing STEP banners in
`sum_strain_energy.bas`, which are lighter than the rest of the repo.

### First-run checklist additions
- Excel: the Summary percents match the Messages window, the color scale shows, and the
  Detail AutoFilter works.
- CSV: it opens cleanly in Excel, and a title with a comma stays in a single column.
- If you can, run on a machine without Excel and confirm the CSV fallback. Otherwise skip
  this check, since a closed Excel still starts fine through `CreateObject`.

### Status
Revision 2 approved and implemented in `sum_strain_energy.bas`. Not yet run in FEMAP.
Implementation notes:
- Results are kept in module-level arrays: `gSE/gPC/gN(s, b)`, sized up front, because
  WinWrap's `ReDim Preserve` only grows the last dimension.
- `DetailRow` fills one row for both the Excel Detail sheet and the CSV, so the two always
  match.
- Excel constants (`xlEdgeTop`, `xlCenter`, …) are written as numbers with comments, since a
  late-bound COM object has no constants.
- The model path comes from `feAppGetModel` + `feAppGetModelName`, inside an error trap.
  If the 64-bit model ID gives a type mismatch, the CSV default falls back to
  `C:\temp\strain_energy.csv`.
