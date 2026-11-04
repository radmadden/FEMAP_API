# Plan: make_bolt_connections.bas

Build CBUSH bolted connections between two dissimilar shell meshes, one run per bolt pattern.
See `research-femap-bolt-connections.md` for the API/engineering basis.

## What the user supplies at run time
1. **CBUSH property ID** (must already exist; the macro validates `feProp.Get` succeeds and warns
   if the property type isn't a spring/bush type).
2. **Offset mode** — `0` midplane spanning (default), `1` faying-surface offset. (Prompt defaults
   to the `OFFSET_MODE` constant, mirroring the thermal macro's SAG_MODE prompt.)
   2a. If mode `1`: **orientation CSys** for any zero-length (coincident) CBUSH — enter an existing
   rectangular CSys ID to reuse for all, or `0` to auto-create one per bolt aligned to its axis.
   (Only used for bolts whose two reference grids actually coincide; a mode-1 bolt with a real plate
   gap still self-orients.)
3. **Side A plate elements** (the shell) — `feSet.Select(FT_ELEM, …)`.
4. **Side A bolt Points** — `feSet.Select(FT_POINT, …)`.
5. **Side B plate elements** — `feSet.Select(FT_ELEM, …)`.
6. **Side B bolt Points** — `feSet.Select(FT_POINT, …)`.
7. **Numbering:** first new node ID, node stride per bolt (≥2), first new element ID, element
   stride per bolt (≥3).

Why select the shell element sets (not just the Points): the pierce/containment search needs
candidate elements, and bounding it per side makes the match unambiguous (the bolt axis pierces
both shells; the A-side set tells us which pierce is the A grid).

## Numbering scheme (predictable, user-controlled)
For bolt `n` (0-based), after pairing:
- ref node A = `startNode + n*nodeStride`, ref node B = `+ 1`
- RBE3 A = `startElem + n*elemStride`, RBE3 B = `+ 1`, CBUSH = `+ 2`
- (zero-length only) orientation CSys = `Info_NextID(FT_CSYS)` at use

Every target node/element ID is checked free before anything is created; if any is taken the macro
reports the clash and aborts (never clobbers). Strides are validated against the counts.

## Algorithm
1. Connect; read all prompts; validate property, strides, ID headroom.
2. **Cache** each side's candidate element geometry in one pass: ID, corner count (3 or 4),
   corner coords, mean-plane **centroid** and **unit normal**. Ignore non-plate topologies.
   (Caching makes the per-bolt search pure arithmetic — fast in WinWrap.)
3. Read side-A and side-B Point coordinates into arrays.
4. **Pair** points: greedy nearest-neighbor, each B used once; warn on count mismatch / large gaps.
5. For each bolt pair (A,B), `axis = normalize(B − A)`:
   a. **Pierce + contain** on side A: for each cached A element, intersect the bolt line with the
      element mean plane (`s = dot(centroid−A, n)/dot(axis, n)`, skip near-parallel), project the
      pierce and corners into a local 2D frame in the mean plane, get `(ξ,η)` (Newton for quad) or
      barycentric `λ` (tri). Prefer a containing element; pick the one whose pierce is nearest the
      point. Same on side B.
   b. **Weights** = `N_i(ξ,η)` (quad) or `λ_i` (tri). Flag near-edge collapse.
   c. **Reference-grid positions**: Mode 0 → pierce point; Mode 1 → `pierce + n_signed·t/2`
      (`n_signed` = shell normal signed toward the other plate; `t = pr.pval(0)` of the pierced
      element's property).
   d. Create ref node A, ref node B (`feNode.Put`).
   e. Create **RBE3 A** and **RBE3 B** (`type=29, topology=13, node(0)=ref, RigidInterpolate=True,
      Release(0,0..5)=1`, `PutNodeList(0, nC, corners, zeros, weights, dof=111000/node)`, `Put`).
   f. Create **CBUSH** (`type=6, topology=0, node(0)=refA, node(1)=refB, propID=cbush`). If the two
      refs are coincident (Mode 1, plates touching) set `SpringUseCID=True, SpringCID=csysID` using
      the supplied CSys or an auto-built axis-aligned one; otherwise it self-orients.
6. `feViewRegenerate`; report counts, offset mode, and warnings (unpaired points, unpierced points,
   near-edge weights, and the F×gap verification reminder).

## Helper routines
- Vector ops: `VSub, VAdd, VScale, VDot, VCross, VNorm`.
- `CacheSide` — build the element geometry arrays for one selection.
- `ElemNormalCentroid` — mean-plane normal (quad: diagonal cross; tri: edge cross) + centroid.
- `FindPierce` — pierce + containment + weights for one point against one cached side; returns the
  chosen element index, weights, pierce point, normal, thickness.
- `InvMapQuad` (Newton), `ShapeQuad`, `BaryTri`, `LocalUV`.
- `MakeRefNode`, `MakeRBE3`, `MakeCBUSH`, `MakeAxisCSys`.
- `IDFree` — true if a node/elem ID is unused.

## Assumptions / limits (documented in the header)
- Plate candidates are QUAD4/8 (corners 0..3) or TRIA3/6 (corners 0..2); midside nodes ignored for
  weighting (loads go to corners — standard for a bolt spider).
- Thickness for Mode 1 comes from the property's nominal `pval(0)`; variable/nodal thickness is not
  read (flagged).
- One bolt ↔ one element per shell. A bolt whose axis misses both shells, or lands outside every
  candidate element, is skipped and reported.
- The F×gap moment-balance question is **unverified** — see research doc; the macro prints the
  reminder.

## Status
Drafted, not yet run in FEMAP. First-run checklist: confirm RBE3 shows as interpolation with the
expected weights in the Entity Editor; confirm CBUSH orientation (spanning self-orient vs
zero-length CSys); run the single-bolt GPFORCE/SPC moment-balance check from the research doc.
