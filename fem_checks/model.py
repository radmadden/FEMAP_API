"""Everything the tool does with the model deck itself.

  * read the FEMAP-exported deck with pyNastran (Simcenter dialect, mode='nx')
  * model summary: card counts, element quality, pyNastran mass properties
  * pick / validate the kinematic mount grid
  * pull the mass/MPC-relevant case control out of the FEMAP deck so the check decks
    keep it (e.g. the user's 1G template carries "NSM = 2": dropping it would drop mass)
  * write the bulk data once (and a thermal variant with uniform CTE)

FEMAP decks end with a checksum on the ENDDATA line (e.g. "ENDDATA b482e8c1"). The
user confirmed decks without it run on their Simcenter install, so the decks this tool
writes simply don't carry one. read_model() strips it before pyNastran sees the file.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from pyNastran.bdf.bdf import BDF
from pyNastran.bdf.mesh_utils.mass_properties import mass_properties

# Elements that give their grids rotational stiffness. A mount grid touching only
# solids has no rotational stiffness, so the R1-R3 unit-rotation subcases would
# "pass" without testing anything (research-fem-checks.md, "Mount grid trap").
ROTATIONAL_ELEMENTS = {"CQUAD4", "CQUAD8", "CQUADR", "CQUAD", "CTRIA3", "CTRIA6",
                       "CTRIAR", "CBAR", "CBEAM", "CBEND", "CBUSH"}

# Element types the quality summary covers. pyNastran's element_quality fills some
# other types (e.g. CBUSH) with 0.0 instead of NaN, which would pollute the statistics.
QUALITY_TYPES = {"CQUAD4", "CQUAD8", "CQUADR", "CTRIA3", "CTRIA6", "CTRIAR",
                 "CHEXA", "CPENTA", "CTETRA", "CPYRAM"}

# Case-control commands that change the model itself (mass, MPCs, DMIG). These are
# copied from the FEMAP deck into every check deck. Everything else (SPC, LOAD,
# output requests, METHOD...) is replaced by the check's own case control.
CARRIED_CASE_CONTROL = ("NSM", "MPC", "K2GG", "M2GG", "B2GG", "K42GG", "K2PP", "M2PP", "B2PP")

# Model PARAMs each check deck sets for itself. All other PARAMs (WTMASS, K6ROT,
# COUPMASS, ...) stay in the bulk data untouched because they change the answers.
PARAMS_SET_BY_CHECKS = ("AUTOSPC", "GRDPNT", "POST", "OGEOM", "PRGPST")

# Under RIGID=LAGRAN, rigid element IDs must be <= 1e8 (qrg ~26012 remark 3).
LAGRAN_MAX_ID = 100_000_000

_ENDDATA_RE = re.compile(r"^\s*ENDDATA\b.*$", re.IGNORECASE)


@dataclass
class ModelInfo:
    path: str
    n_grids: int
    n_elements: int
    n_rigid: int
    n_masses: int
    card_counts: dict
    element_counts: dict
    mass: float
    cg: list
    inertia_cg: list            # pyNastran [Ixx, Iyy, Izz, Ixy, Ixz, Iyz] about CG (info only)
    bbox_min: list
    bbox_max: list
    size: float                 # bounding-box diagonal, the model length scale L
    carried_case_control: list  # case-control lines copied into every check deck
    params: dict                # model PARAM name -> value (as read)
    wtmass: float
    e_max: float                # largest Young's modulus in the model (thermal stress scale)
    quality: dict = field(default_factory=dict)  # metric -> dict(stats, worst=[(eid, value)])
    geomcheck: list = field(default_factory=list)  # rows: limit exceedances vs Nastran defaults
    trace: dict = field(default_factory=dict)      # title/subtitle/FEMAP model for traceability
    notes: list = field(default_factory=list)


# --------------------------------------------------------------------- reading
def _strip_checksum(deck: Path, work_dir: Path) -> Path:
    """Return a path pyNastran can read: if the ENDDATA line carries text after the
    keyword (FEMAP's checksum), write a copy with a bare ENDDATA into work_dir."""
    text = deck.read_text(errors="replace").splitlines()
    changed = False
    for i, line in enumerate(text):
        if _ENDDATA_RE.match(line) and line.strip().upper() != "ENDDATA":
            text[i] = "ENDDATA"
            changed = True
    if not changed:
        return deck
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / f"{deck.stem}_nochecksum{deck.suffix}"
    out.write_text("\n".join(text) + "\n")
    return out


def read_model(deck: Path, work_dir: Path) -> BDF:
    """Read the FEMAP deck. xref=True so elements know their nodes/properties.
    Raises with pyNastran's own message if a card can't be read."""
    readable = _strip_checksum(Path(deck), Path(work_dir))
    model = BDF(debug=False, mode="nx")
    # INCLUDEs in the FEMAP deck are resolved relative to the original deck folder.
    model.read_bdf(str(readable), xref=True, punch=False, validate=True)
    return model


def read_trace(deck: Path) -> dict:
    """Traceability details from the FEMAP deck, copied into every check deck and the report:
      title, subtitle      the TITLE / SUBTITLE case-control lines (first occurrence)
      femap_model, femap_date, femap_version   FEMAP's '$ From Model :' header block
      nastran_statements   NASTRAN / INIT lines ahead of the executive control
                           (FEMAP writes e.g. 'NASTRAN SYSTEM(442)=-1,SYSTEM(319)=1');
                           these are carried so the check runs use the same solver
                           system settings as the user's own runs."""
    out = {"deck": str(Path(deck).resolve()), "nastran_statements": []}
    in_exec = True
    for line in Path(deck).read_text(errors="replace").splitlines():
        u = line.strip().upper()
        if in_exec and (u.startswith("NASTRAN ") or u.startswith("INIT ")):
            out["nastran_statements"].append(line.strip())
        if u.startswith(("SOL ", "ID ", "CEND")):
            in_exec = False
        m = re.match(r"\s*(TITLE|SUBTITLE)\s*=\s*(.*)$", line, re.IGNORECASE)
        if m and m.group(1).lower() not in out:
            out[m.group(1).lower()] = m.group(2).strip()
        m = re.match(r"\$\s*(From Model|Date|Version)\s*:\s*(.*)$", line.strip(), re.IGNORECASE)
        if m:
            key = {"from model": "femap_model", "date": "femap_date",
                   "version": "femap_version"}[m.group(1).lower()]
            out.setdefault(key, m.group(2).strip())
        if u.startswith("BEGIN") and "BULK" in u and "femap_model" in out:
            break
        if u.startswith("GRID"):  # the FEMAP header block comes before the first card
            break
    return out


def carried_case_control(deck: Path) -> list[str]:
    """First occurrence of each CARRIED_CASE_CONTROL command between CEND and BEGIN BULK."""
    lines = Path(deck).read_text(errors="replace").splitlines()
    out, seen, in_cc = [], set(), False
    for line in lines:
        u = line.strip().upper()
        if u.startswith("CEND"):
            in_cc = True
            continue
        if u.startswith("BEGIN") and "BULK" in u:
            break
        if not in_cc or u.startswith("$"):
            continue
        m = re.match(r"([A-Z0-9]+)\s*(\(.*?\))?\s*=", u)
        if m and m.group(1) in CARRIED_CASE_CONTROL and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(line.strip())
    return out


# ---------------------------------------------------- element quality limits
# Simcenter's own default element-geometry limits: GEOMCHECK (qrg ~8150, remark 2).
# Nastran applies these on every run and prints INFORM messages for elements beyond
# them (it does not stop unless MSGTYPE=FATAL; solid aspect-ratio checks are FATAL by
# default). Only the tests whose definition matches what pyNastran computes are
# compared here: interior angles and edge aspect ratio. Nastran's skew, taper and warp
# are defined differently from pyNastran's skew/taper/warp-angle metrics, so those are
# reported as statistics only. (FEMAP's Tools > Check > Distortion uses its own,
# user-set tolerances; the tool doesn't try to mirror them.)
GEOMCHECK_LIMITS = [
    # (element types, pyNastran metric, '<' or '>', limit, Nastran keyword)
    ({"CQUAD4", "CQUADR"}, "min_interior_angle", "<", 30.0, "Q4_IAMIN"),
    ({"CQUAD4", "CQUADR"}, "max_interior_angle", ">", 150.0, "Q4_IAMAX"),
    ({"CQUAD4"}, "max_aspect_ratio", ">", 100.0, "QAD_AR"),
    ({"CTRIA3", "CTRIAR"}, "max_interior_angle", ">", 160.0, "T3_IAMAX"),
    ({"CQUAD8"}, "min_interior_angle", "<", 30.0, "Q8_IAMIN"),
    ({"CQUAD8"}, "max_interior_angle", ">", 150.0, "Q8_IAMAX"),
    ({"CQUAD8"}, "max_aspect_ratio", ">", 100.0, "Q8_AR"),
    ({"CTRIA6"}, "min_interior_angle", "<", 10.0, "TA6_IAMN"),
    ({"CTRIA6"}, "max_interior_angle", ">", 160.0, "TA6_IAMX"),
]


def geomcheck_exceedances(model: BDF, quality: dict, log: Callable[[str], None] = print) -> list:
    """Rows [keyword, element types, limit, n checked, n beyond, worst 10 element IDs]."""
    try:
        from pyNastran.bdf.mesh_utils.delete_bad_elements import element_quality
        q = element_quality(model)
    except Exception as exc:
        log(f"GEOMCHECK comparison skipped: {exc}")
        return []
    eids = np.array(sorted(model.elements), dtype=int)
    etypes = np.array([model.elements[e].type for e in eids])
    rows = []
    for types, metric, op, limit, kw in GEOMCHECK_LIMITS:
        if metric not in q:
            continue
        mask = np.isin(etypes, list(types))
        if not mask.any():
            continue
        v = np.asarray(q[metric], dtype=float)[mask]
        if "angle" in metric:
            v = np.degrees(v)
        ok = ~np.isnan(v)
        bad = (v < limit) if op == "<" else (v > limit)
        bad &= ok
        order = np.argsort(v[bad]) if op == "<" else np.argsort(v[bad])[::-1]
        worst = eids[mask][bad][order][:10].tolist()
        rows.append([kw, "/".join(sorted(types)), f"{op} {limit:g}", int(ok.sum()),
                     int(bad.sum()), ", ".join(str(e) for e in worst) or "-"])
    return rows


# --------------------------------------------------------------------- summary
def _grid_xyz(model: BDF) -> tuple[np.ndarray, np.ndarray]:
    """(node ids sorted, xyz in basic) for all GRIDs."""
    nids = np.array(sorted(model.nodes), dtype=int)
    xyz = np.array([model.nodes[n].get_position() for n in nids], dtype=float)
    return nids, xyz


def e_max(model: BDF) -> float:
    """Largest Young's modulus (or stiffness term) of any structural material."""
    vals = []
    for mat in model.materials.values():
        t = mat.type
        if t == "MAT1":
            vals.append(mat.e or 0.0)
        elif t == "MAT2":
            vals += [mat.G11 or 0.0, mat.G22 or 0.0]
        elif t == "MAT8":
            vals += [mat.e11 or 0.0, mat.e22 or 0.0]
        elif t == "MAT9":
            vals += [mat.G11 or 0.0, mat.G22 or 0.0, mat.G33 or 0.0]
        elif t == "MAT11":
            vals += [mat.e1 or 0.0, mat.e2 or 0.0, mat.e3 or 0.0]
        elif t == "MAT3":
            vals += [mat.ex or 0.0, mat.eth or 0.0, mat.ez or 0.0]
    return float(max(vals)) if vals else float("nan")


def model_info(model: BDF, deck: Path, quality: bool = True,
               log: Callable[[str], None] = print) -> ModelInfo:
    nids, xyz = _grid_xyz(model)
    bmin, bmax = xyz.min(axis=0), xyz.max(axis=0)
    elem_counts: dict[str, int] = {}
    for e in model.elements.values():
        elem_counts[e.type] = elem_counts.get(e.type, 0) + 1
    for e in model.rigid_elements.values():
        elem_counts[e.type] = elem_counts.get(e.type, 0) + 1
    for e in model.masses.values():
        elem_counts[e.type] = elem_counts.get(e.type, 0) + 1
    card_counts = {k: v for k, v in sorted(model.card_count.items())}
    mass, cg, inertia = mass_properties(model)
    params = {k: p.values[0] if len(p.values) == 1 else p.values for k, p in model.params.items()}
    info = ModelInfo(
        path=str(deck), n_grids=len(nids), n_elements=len(model.elements),
        n_rigid=len(model.rigid_elements), n_masses=len(model.masses),
        card_counts=card_counts, element_counts=dict(sorted(elem_counts.items())),
        mass=float(mass), cg=[float(c) for c in cg], inertia_cg=[float(v) for v in inertia],
        bbox_min=bmin.tolist(), bbox_max=bmax.tolist(),
        size=float(np.linalg.norm(bmax - bmin)),
        carried_case_control=carried_case_control(deck), params=params,
        trace=read_trace(deck),
        wtmass=float(params.get("WTMASS", 1.0)), e_max=e_max(model),
    )
    if quality:
        info.quality = element_quality_summary(model, log)
        info.geomcheck = geomcheck_exceedances(model, info.quality, log)
    big = [eid for eid in model.rigid_elements if eid > LAGRAN_MAX_ID]
    if big:
        info.notes.append(f"{len(big)} rigid element IDs exceed 1e8; RIGID=LAGRAN (thermal check) "
                          f"will refuse them (qrg ~26012). First few: {big[:10]}")
    return info


def element_quality_summary(model: BDF, log: Callable[[str], None] = print,
                            n_worst: int = 20) -> dict:
    """Per-metric statistics from pyNastran's element_quality (shells/solids/bars).

    Returns {metric: {count, min, max, mean, p95, worst: [(eid, type, value), ...]}}.
    'worst' means largest value except for min_interior_angle/min_edge_length where
    smaller is worse. Only shells and solids (QUALITY_TYPES) are summarized; NaN entries
    (metric not defined for that element type, e.g. aspect ratio on a CHEXA) are
    ignored. Angles come back from pyNastran in radians and are reported in degrees."""
    try:
        from pyNastran.bdf.mesh_utils.delete_bad_elements import element_quality
        q = element_quality(model)
    except Exception as exc:  # quality is informational; never stop the run for it
        log(f"Element quality skipped: {exc}")
        return {}
    eids = np.array(sorted(model.elements), dtype=int)
    etypes = np.array([model.elements[e].type for e in eids])
    out = {}
    for name, arr in q.items():
        arr = np.asarray(arr, dtype=float)
        if arr.shape[0] != len(eids):
            continue
        ok = ~np.isnan(arr) & np.isin(etypes, list(QUALITY_TYPES))
        if not ok.any():
            continue
        vals = arr[ok]
        smaller_is_worse = name in ("min_interior_angle", "min_edge_length")
        order = np.argsort(arr[ok])
        if not smaller_is_worse:
            order = order[::-1]
        idx = np.flatnonzero(ok)[order[:n_worst]]
        if "angle" in name or name in ("dideal_theta",):
            conv = np.degrees  # pyNastran returns radians
        else:
            conv = lambda x: x  # noqa: E731
        out[name] = dict(
            count=int(ok.sum()), min=float(conv(vals.min())), max=float(conv(vals.max())),
            mean=float(conv(vals.mean())), p95=float(conv(np.percentile(vals, 95))),
            worst=[(int(eids[i]), str(etypes[i]), float(conv(arr[i]))) for i in idx],
            values=conv(vals).tolist(),
        )
    return out


# ----------------------------------------------------------------- mount grid
@dataclass
class MountChoice:
    grid: int
    xyz: list
    distance_to_cg: float
    distance_fraction: float     # distance / model size
    auto: bool
    rotation_grids: list         # grids whose rotations are checked in unit-disp


def _rotation_grids(model: BDF) -> set[int]:
    grids = set()
    for e in model.elements.values():
        if e.type in ROTATIONAL_ELEMENTS:
            grids.update(n for n in e.node_ids if n)
    for r in model.rigid_elements.values():
        if r.type == "RBE2":
            grids.add(r.gn)
    return grids


def _spc_grids(model: BDF) -> set[int]:
    """Grids with a PERMANENT single-point constraint (GRID card PS field).

    SPC/SPC1 cards in the model's own sets don't count: the check decks select only
    their own SPC set, so those cards are inactive. (The user's model has its mount grid
    199999 in SPC sets 2 and 3; refusing it for that would be wrong.) The PS field is
    always active, and an SPC1 123456 on top of it is harmless, but a grid with PS
    rotations fixed couldn't carry the unit-rotation SPCD, so it's refused."""
    return {nid for nid, node in model.nodes.items() if node.ps not in ("", None, 0, "0")}


def _mount_problems(model: BDF, nid: int, rot: set, dep: dict, spc: set) -> list[str]:
    p = []
    if nid not in model.nodes:
        return [f"grid {nid} does not exist"]
    if nid not in rot:
        p.append(f"grid {nid} is not on a shell/beam/bar/bush element or an RBE2 independent "
                 "grid, so it has no rotational stiffness (unit rotations would test nothing)")
    if nid in dep:
        p.append(f"grid {nid} is a dependent (m-set) grid of a rigid element/MPC "
                 f"(components {dep[nid]}); Nastran cannot SPC it")
    if nid in spc:
        p.append(f"grid {nid} has a permanent constraint (PS={model.nodes[nid].ps}) on its GRID card")
    if model.nodes[nid].cd not in (0, None):
        p.append(f"grid {nid} has output coordinate system CD={model.nodes[nid].cd}; "
                 "the mount must use CD=0 so SPCD directions are basic X/Y/Z")
    return p


def choose_mount(model: BDF, info: ModelInfo, requested: int = 0) -> MountChoice:
    """requested=0 -> auto-pick the qualifying grid nearest the CG.
    Raises ValueError with plain-English reasons if no grid qualifies."""
    rot = _rotation_grids(model)
    dep = model.get_dependent_nid_to_components()  # rigid elements (MPCs not selected here)
    spc = _spc_grids(model)
    cg = np.array(info.cg)
    if requested:
        problems = _mount_problems(model, requested, rot, dep, spc)
        if problems:
            raise ValueError("Mount grid rejected: " + "; ".join(problems))
        nid = requested
    else:
        cands = [n for n in rot if n in model.nodes and n not in dep and n not in spc
                 and model.nodes[n].cd in (0, None)]
        if not cands:
            raise ValueError("No grid qualifies as a kinematic mount (needs rotational "
                             "stiffness, not dependent, no SPC, CD=0). Enter one by hand.")
        xyz = np.array([model.nodes[n].get_position() for n in cands])
        nid = int(cands[int(np.argmin(np.linalg.norm(xyz - cg, axis=1)))])
    pos = model.nodes[nid].get_position()
    dist = float(np.linalg.norm(pos - cg))
    return MountChoice(grid=nid, xyz=pos.tolist(), distance_to_cg=dist,
                       distance_fraction=dist / info.size if info.size else float("nan"),
                       auto=not requested, rotation_grids=sorted(rot))


# --------------------------------------------------------------- bulk writing
def bulk_text(model: BDF) -> str:
    """The model's bulk data as written by pyNastran in large-field (16 character)
    format, so node coordinates and properties keep their precision. The PARAMs in
    PARAMS_SET_BY_CHECKS are removed (each check deck sets its own)."""
    saved = {k: model.params.pop(k) for k in PARAMS_SET_BY_CHECKS if k in model.params}
    # Don't let pyNastran re-write the FEMAP case control: we discard it anyway, and
    # pyNastran's writer asserts on case-control lines longer than ~68 characters
    # (bdf/subcase.py), which real FEMAP decks have (first user run stopped with
    # "AssertionError: len(msg)=77"). Detach it for the write, then put it back.
    saved_cc = model.case_control_deck
    model.case_control_deck = None
    try:
        buf = io.StringIO()
        model.write_bdf(buf, size=16, is_double=False, enddata=False, close=False)
        text = buf.getvalue()
    finally:
        model.params.update(saved)
        model.case_control_deck = saved_cc
    # write_bdf also writes executive and case control; keep only what follows BEGIN BULK.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        u = line.strip().upper()
        if u.startswith("BEGIN") and "BULK" in u:
            lines = lines[i + 1:]
            break
    lines = [x for x in lines if not _ENDDATA_RE.match(x)]
    return "\n".join(lines) + "\n"


def make_thermal_uniform(model: BDF, alpha: float) -> list[str]:
    """Overwrite every CTE with ``alpha`` and every reference temperature with 0, in place.
    Call this on a freshly read copy, never on the model used for the other decks.

    - MAT1 A; MAT2 A1=A2=alpha, A3 (shear) = 0; MAT3 AX=ATH=AZ; MAT8 A1=A2;
      MAT9 A1..A3 = alpha, A4..A6 (shear) = 0; MAT11 A1..A3. TREF = 0 everywhere.
    - PCOMP/PCOMPG TREF = 0.
    - MATT* removed: with TEMP(INIT) defined, MATTi properties are evaluated at the
      initial temperature (qrg ~29940 remark 8), which could make alpha non-uniform.
    - Every rigid element gets ALPHA = alpha (decision #4; RIGID=LAGRAN is set in the
      deck). Rigid-element TREF is left alone: the Simcenter RBE2 format has no TREF
      field (qrg ~112883).
    Returns a log of what was changed."""
    log = []
    counts: dict[str, int] = {}
    for mat in model.materials.values():
        t = mat.type
        if t == "MAT1":
            mat.a, mat.tref = alpha, 0.0
        elif t == "MAT2":
            mat.a1, mat.a2, mat.a3, mat.tref = alpha, alpha, 0.0, 0.0
        elif t == "MAT3":
            mat.ax, mat.ath, mat.az, mat.tref = alpha, alpha, alpha, 0.0
        elif t == "MAT8":
            mat.a1, mat.a2, mat.tref = alpha, alpha, 0.0
        elif t == "MAT9":
            mat.A = [alpha, alpha, alpha, 0.0, 0.0, 0.0]
            mat.tref = 0.0
        elif t == "MAT11":
            mat.a1, mat.a2, mat.a3, mat.tref = alpha, alpha, alpha, 0.0
        else:
            log.append(f"material {mat.mid} ({t}) left unchanged (no CTE handling for {t})")
            continue
        counts[t] = counts.get(t, 0) + 1
    for prop in model.properties.values():
        if prop.type in ("PCOMP", "PCOMPG"):
            prop.tref = 0.0
            counts[prop.type] = counts.get(prop.type, 0) + 1
    for attr in ("MATT1", "MATT2", "MATT3", "MATT4", "MATT5", "MATT8", "MATT9", "MATT11"):
        d = getattr(model, attr, None)
        if d:
            log.append(f"removed {len(d)} {attr} (temperature-dependent material) entries")
            d.clear()
    for r in model.rigid_elements.values():
        if hasattr(r, "alpha"):
            r.alpha = alpha
            counts[r.type] = counts.get(r.type, 0) + 1
        else:
            log.append(f"rigid element {r.eid} ({r.type}) has no ALPHA field; left unchanged")
    log.insert(0, "uniform CTE = %g, TREF = 0 set on: %s" % (
        alpha, ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "nothing"))
    return log


def used_ids(model: BDF) -> set[int]:
    """Set IDs already used by loads/SPCs/temperatures/methods (to avoid collisions)."""
    ids = set(model.loads) | set(model.load_combinations) | set(model.spcs) \
        | set(model.spcadds) | set(model.tempds) | set(model.methods) | set(model.mpcs) \
        | set(model.dloads) | set(model.dload_entries)
    for attr in ("spcds", "temps"):
        d = getattr(model, attr, None)
        if d:
            ids |= set(d)
    return ids


def reserved_base(model: BDF, start: int = 9_000_000, block: int = 1000) -> int:
    """First multiple of 1,000,000 at or above ``start`` whose next ``block`` IDs are
    all unused, so the check decks' SPC/LOAD/TEMPD/EIGRL IDs never collide."""
    taken = used_ids(model)
    base = start
    while any(base + k in taken for k in range(block)):
        base += 1_000_000
    return base


def spc_set_exists(model: BDF, sid: int) -> bool:
    return sid in model.spcs or sid in model.spcadds
