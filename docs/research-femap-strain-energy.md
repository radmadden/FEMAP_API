# Research: summing element strain energy over selected elements (FEMAP API)

Goal: a macro that takes a user-selected set of elements and one or more output sets, and
reports the **total element strain energy** of those elements, plus their **share of the
model total**. Typical uses: which part of the structure stores the energy in a static load
case, and the modal strain energy fraction per mode (which component drives each mode).

Everything needed can be scripted. All findings were checked against `api.pdf` (FEMAP API
Guide 2025). Line numbers below refer to the extracted text of that guide.

---

## Key API findings (confirmed in api.pdf)

### Finding the strain energy output vector: `ResultsIDQuery.Elemental`
- Since v2020.1, output vector IDs depend on the solver and the version, so they should be
  looked up, not hard-coded (api ~1723, "Working with Output in Femap v2020.1 and above").
- `Set idq = App.feResultsIDQuery` then `idq.Elemental(zVecElemOther)` returns the vector ID,
  or `FE_FAIL` (api ~75050). The remarks say it covers "almost any Strain Energy, Heat
  Transfer, Kinetic Energy…" quantity.
- `zVecElemOther` constants for strain energy (api ~74100):
  - `VEO_STRAIN_ENERGY = 0` (absolute element strain energy, which is the one to sum)
  - `VEO_STRAIN_ENERGY_PERCENT = 1` (Nastran's per-element percent of the subcase total)
  - `VEO_STRAIN_ENERGY_DENSITY = 2`
- The lookup takes **no output-set argument**. It returns the ID FEMAP uses for that
  quantity, and whether a given output set actually holds that vector is a separate check
  (see AddColumnV2 below).

### Reading the values: Results Browsing Object (`App.feResults`)
The guide calls this "by far the fastest" way to review large amounts of output (api ~61115).
- `rbo.AddColumnV2(setID, vecID, bAddComponentsCorners, nColumnsAdded, nColumnIndices)`
  (api ~61119). `nColumnIndices(0)` is the column for the requested vector. It returns
  **`FE_NOT_EXIST` if that vector isn't in the output set**, which is how a set with no ESE
  output is detected.
- `rbo.DataNeeded(8, elemSetID)` (api ~61393) limits the rows to a set of element IDs. It must
  be called before `Populate`.
- `rbo.Populate()` loads the data. After that, `rbo.GetColumn(colIdx, nIDs, dVals)`
  (api ~62217) returns two variant arrays covering every row: entity IDs and values.
- `rbo.Clear()` resets the object so it can be reused for the next output set (pattern from
  the feSelectOutput2V2 example, api ~38640).
- `GetColumnMinMax` (api ~62308) and `SendToDataTable` (api ~59900) also exist. They would
  be useful for reporting the element with the most energy, or for sending the per-element
  values to the Data Table.

### Selection
- Elements: `Set e = App.feSet : e.Select(FT_ELEM, True, title)` (api ~54182). This is the
  standard FEMAP picker, so it already supports picking by group, property, material, and so
  on. `e.IsAdded(id)` (api ~54049) tests membership, and `e.Count()` gives the count.
- Output sets: `FT_OUT_CASE = 28` (api ~7768), so the same `Set.Select` gives a multi-select
  of output sets. The alternative, `App.feSelectOutput2V2` (api ~38640), picks sets and
  vectors together. We don't need it because the vector is fixed.
- Groups: `Set.AddGroup(groupID)` (api ~52706). Only needed if we want a breakdown per group.
- Output set title for the report: `App.feOutputSet.Get(id)` then `.title`.

### No built-in "sum over selection"
No API call returns a summed result over a set of elements. Nothing in Results Browsing,
Output Set, or Output Vector does this, so the macro has to sum the values itself. It's a
single pass over `GetColumn`.

---

## Engineering notes

### What Nastran writes
- Element strain energy is only present when the solve requested it: `ESE = ALL`, or
  `ESE = n` with a SET, in Case Control. (In FEMAP this is the "Strain Energy" output request
  for elements in the analysis set.) If ESE wasn't requested, `AddColumnV2` returns
  `FE_NOT_EXIST` for every set.
- If ESE was requested for only some elements, the "model total" is the total over those
  elements. Nastran's own `PERCENT OF TOTAL` is based on the subcase total that Nastran
  computed, which can differ from our total in that case.
- **Static subcases:** ESE is ½·uᵀKu per element, and summing over elements is exact.
- **Modal (SOL 103):** ESE is per mode for the normalized mode shape. The absolute value
  depends on the normalization (MASS or MAX), but the **fraction** doesn't, so the percent is
  the useful number. One output set per mode, which is why the macro needs to handle several
  output sets.
- **Complex or frequency-response output:** Nastran writes real, imaginary, and average ESE as
  separate quantities. `Elemental(VEO_STRAIN_ENERGY)` returns one vector ID, and it's not
  verified which component that is for complex sets. Treat static and modal as the
  supported cases.
- Element types with no stiffness of their own (RBE2, RBE3, CONM2) carry no ESE. Including
  them in the selection is harmless. They just don't count toward "elements with data".

### Percent of total: compute it, or read Nastran's percent vector?
- **Compute it** (sum the selected elements ÷ sum of every element that has output). This
  needs one column over all elements with no `DataNeeded` filter, and gives the total as a
  number to report.
- **Sum `VEO_STRAIN_ENERGY_PERCENT`** over the selection. Nastran normalizes this against its
  own subcase total, so it's correct even when ESE was requested for a subset. It's one more
  column, and the extra cost is small.
- The two agree when `ESE = ALL`.

### Cost
One Populate per output set for all elements is fast, even for models with around 1M
elements, because the RBO is built for this. Calling `DataNeeded` with the selection would be
faster, but then there's no model total.

---

## Open design questions (for the plan)
1. **Selection:** a plain element pick, or also a per-group (or per-property) breakdown in one
   run? >> yes to all of that
2. **Percent basis:** our computed total, Nastran's percent vector, or both? >>both
3. **Output:** Messages window only, CSV, Data Table, or a new output vector that holds the
   selection's values for contour plotting? >> output window is great
4. **Extras:** strain energy density? The element with the most energy in the selection
   (`GetColumnMinMax`)? >>no
5. **Complex or frequency-response output sets:** support them (which component?) or skip
   them with a warning? >>no
