# Research: CBUSH bolted connections between dissimilar shell meshes (FEMAP API)

Goal: a macro that, given a pair of bolt-center geometry **Point** sets (one per side of a joint)
and the two shell element sets, automatically builds, for each bolt:

- a weighted **RBE3 spider** on each shell (reference grid = dependent, the shell element's
  corner nodes = independent grids), and
- a **CBUSH** of a specified property spanning the joint between the two reference grids,

for whole bolt patterns in one run, with user-specified ID numbering. Everything needed is
scriptable; verified against `api.pdf` (FEMAP API Guide 2025). Line numbers below are into the
extracted text of that guide.

---

## Key API findings (confirmed in api.pdf)

### RBE3 element — created through the Element object (`Set el = App.feElem`)
An RBE3 is a **Rigid interpolation** element:
- `el.type = 29` (Rigid, `FET_L_RIGID`) and `el.topology = 13` (RigidList). (Element type /
  topology tables, api ~101900, ~102028; worked example at api ~2331.)
- `el.node(0)` = the **reference grid** (for an interpolation element this is the *dependent*
  node). Rigid elements store their *other* nodes in a **separate node list**, not in `node[]`
  (api ~101918, Accessing Element Nodes: "0=Independent, dependent nodes use element lists").
- `el.RigidInterpolate = True` flags it as an interpolation (RBE3) element rather than an RBE2
  (api ~101951, `BOOL RigidInterpolate`).
- `el.Release(0, 0..5)` sets the DOF flags of `node(0)` — i.e. the reference grid's dependent
  DOFs, **REFC** (api ~101924, "also used for the DOFs of the independent node of Rigid
  Element using [0][...]"; example sets `Release(0,0..2)=1`).
- The independent grids, their **weights**, and their contributing DOFs go in list 0 via
  `el.PutNodeList(0, nodecount, node, face, weight, dof)` (api ~102654). `node` = independent
  node IDs; `face` is "not used - must be NULL" (pass zeros); `weight` = per-node weighting
  factors (REAL8); `dof` = **6 sequential flags per node** (`dof[0..5]` for node 0, `dof[6..11]`
  for node 1, …). `ClearNodeList(-1)` wipes lists if the object is reused (api ~102715).
- Store with `el.Put(id)` **after** `PutNodeList` ("you must call Put() if you want to store the
  updated lists", api ~102700).

Worked RBE3 example in the guide (api ~2300–2345): compute a centroid node, then
`feElem.Type=29 : feElem.topology=13 : feElem.node(0)=refNode : feElem.Release(0,0..2)=1`,
`PutNodeList(0, n, vNodes, vFace, vWeight, vDOF)`, `Put(elemID)`. This is exactly the spider
we want — reference grid at the attach point, surrounding nodes as weighted independents.

### CBUSH element — a Spring/Damper element referencing a PBUSH/CBUSH property
- `el.type = 6` (Spring/Damper, `FET_L_SPRING`) and `el.topology = 0` (Line2) (api ~42610).
- `el.propID` = the existing CBUSH property ID. FEMAP writes a property as CBUSH when its
  property-object CBUSH flag is set (`1=CBUSH`, api ~122205); creating that property is the
  user's job (the macro only references it).
- `el.node(0)`, `el.node(1)` = the two reference grids. When the ends are **distinct**, the
  element self-orients along the GA→GB line — no orientation CSys needed. (This is why the
  default midplane-spanning layout is the robust one.)
- When the ends are **coincident** (a true zero-length CBUSH), orientation must be supplied.
  Spring orientation can come from a coordinate system: set `el.SpringUseCID = True` and
  `el.SpringCID = <csysID>` (spring flags, api ~102096: "release(2)=True - Use SpringCID for
  orientation"; and api ~33726: a CSys "should … be used … for Spring/Damper elements
  referencing a CBUSH Property"). Keep `el.SpringNoOrient = False`.

### Coordinate system aligned to the bolt axis (only for zero-length CBUSH)
`csys.Axis(nCSysType, nAxisDir, origin, Axis)` builds/updates a CSys by a point on one axis
(api ~96041): `Axis(0, 0, origin, ptAlongAxis)` makes a **rectangular** CSys whose **X** points
from `origin` toward `ptAlongAxis`. Use the bolt axis as X, store at `Info_NextID(FT_CSYS)`,
and feed it to `SpringCID`.

### Geometry Points as the bolt-center input
`Set pt = App.fePoint`; after `set.Select(FT_POINT, …)` (api: `FT_POINT = 3`, ~7715), walk the
set and read `pt.x, pt.y, pt.z` — "always specified in global rectangular coordinates" (api
~121666). So bolt centers need **not** coincide with any mesh node.

### Shell thickness for the faying-surface offset
`Set pr = App.feProp : pr.Get(propID)` then **`pr.pval(0)` = plate thickness** for a PSHELL
(plate property type 17/18): confirmed by `Thickness = pval(0)` (api ~123994) and the plate
example `pr.pval(0) = 0.1` (api ~28195).

### Numbering
`feNode.NextEmptyID` / `feElem.NextEmptyID` give the next free IDs (api ~2310, ~2331), but an
entity can be stored at **any** chosen ID with `Put(id)` — so a user-defined start-ID + stride
scheme is just arithmetic feeding `Put`. The macro checks each target ID is free first (via
`feNode.Get`/`feElem.Get` returning not-OK) and aborts rather than clobbering.

### No built-in point→element pierce test
There is no API call that returns "which element does this ray pierce," so the pierce/containment
is computed in the macro from cached element corner coordinates (below). `feMeasureDistance*`
calls exist but don't do containment.

---

## Engineering decisions (from the design discussion)

### Weights = element shape functions at the pierce point (NOT inverse distance)
Inverse-distance weights do **not** put the weighted centroid at the pierce point (e.g. a unit
square with the bolt at (0.25,0.25) gives an IDW centroid near (0.35,0.35)), so RBE3 — which
treats the weighted centroid as the effective load point — would introduce a spurious,
mesh-dependent moment. Using the element **shape functions** `N_i(ξ,η)` at the pierce point as the
weights guarantees `Σ N_i · x_i = x_p` (isoparametric identity), so the weighted centroid lands
exactly on the pierce point, the reference-grid translation equals the element's interpolated
displacement there, and a force at the reference grid distributes as the consistent nodal load.

- **TRIA3**: weights are the **area (barycentric) coordinates** — exact, closed form, no iteration.
- **QUAD4**: bilinear `N_i`; because a general quad is not a parallelogram, invert the
  isoparametric map by a few **Newton iterations** in the element **mean plane** (handles warp).
  `N0=(1-ξ)(1-η)/4, N1=(1+ξ)(1-η)/4, N2=(1+ξ)(1+η)/4, N3=(1-ξ)(1+η)/4`, `ξ,η ∈ [-1,1]`.

### Independent (corner) grids: DOF 123 only
Each corner contributes **translational DOFs 1,2,3** only. Any moment pushed through the RBE3
would be reacted as `w_i·r_i` forces on nodes only an element apart, turning small moments into
large nodal forces. (`dof` flags in `PutNodeList` set to 1,1,1,0,0,0 per node.)

### Reference grid: dependent in 123456 (REFC = 123456)
`Release(0,0..5) = 1`. The reference grid is fully dependent. This matters because the CBUSH
rotational stiffness is ~0 (below): if the reference-grid rotations (456) were left out of REFC
they would be unconstrained → a grounded-rotation singularity. With REFC=123456 the spider defines
the reference rotations from the translational independents (standard RBE3 behavior).

### CBUSH rotational stiffness ~0
Keep the bush's rotational terms zero or very small unless justified; take fastener loads from the
CBUSH force output, and distrust shell stress in the one element next to the spider (a
single-element attachment gives unreliable local stress regardless of weighting).

### Reference-grid placement: two modes (midplane spanning is the default)
- **Mode 0 — midplane spanning (default, robust):** reference grids sit at each shell's pierce
  point (on its mid-surface). The two grids are distinct and separated by the midplane gap, so the
  CBUSH self-orients along the bolt axis. Simple and unambiguous.
- **Mode 1 — faying-surface offset:** each reference grid is pushed `t/2` off its shell midplane
  toward the other plate (along the shell normal), onto the faying surface. This represents the
  real eccentricity (shear at the faying surface acting `t/2` off the midplane) as a couple carried
  by the RBE3. When the plates touch, the two faying points coincide → **zero-length** CBUSH, which
  the macro orients with a per-bolt axis-aligned CSys.

### Open verification item (carried, not assumed)
A gap-spanning CBUSH with zero rotational stiffness is believed to transmit **force but not the
F×gap couple**, so a midplane-spanning bolt may be off in moment equilibrium by `F×gap`. This is
**not yet verified**. Recommended check before trusting either mode: model a single CBUSH with an
applied shear and confirm moment balance in GPFORCE and the SPC forces. The macro therefore keeps
both modes and prints a reminder rather than hard-coding an unverified choice. If the F×gap effect
is confirmed, prefer Mode 1 (faying offset), where the eccentricity couple is represented in the
RBE3 — real physics, not a mesh artifact.

### Near-edge weight collapse (flagged)
If a pierce point falls near an element edge or corner, one or two shape-function weights go to ~0
and the load collapses onto 1–2 nodes → a mesh-dependent hot spot. The macro warns when any
pierce lands within a tolerance of an element boundary.

---

## API calls used (Femap API Guide 2025)
`feFemap`, `fePoint` (x/y/z), `feSet` (Select FT_POINT / FT_ELEM, First/Next),
`feElem` (Get, type, topology, propID, node, Release, RigidInterpolate, PutNodeList,
ClearNodeList, SpringUseCID, SpringCID, Put), `feNode` (Get, x/y/z, Put),
`feProp` (Get, type, pval), `feCSys` (Axis, Put), `Info_NextID(FT_CSYS)`,
`feAppLock`/`feAppUnlock`, `feViewRegenerate`.
