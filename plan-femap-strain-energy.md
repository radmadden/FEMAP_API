# Plan: sum_strain_energy.bas

Sum element strain energy over a user-selected set of elements, broken down by group and by
property, for one or more output sets. See `research-femap-strain-energy.md` for the API and
engineering basis.

Decisions locked from the research review:
- **Selection:** a plain element pick, **plus** a breakdown by group and by property in the
  same run.
- **Percent basis:** **both**. Our computed share (selected ÷ total of every element with
  output) and the sum of Nastran's `VEO_STRAIN_ENERGY_PERCENT` over the same elements.
- **Output:** Messages window only.
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
   the selection gets keyed `elemID → property slot` in a Collection. Only properties with at
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
- `BuildPropMap(App, eSet, …)` builds the property slots and the elemID → slot Collection.

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
