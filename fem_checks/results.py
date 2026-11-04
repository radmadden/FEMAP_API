"""OP2 result extraction (pyNastran) and the theoretical displacement fields.

pyNastran 1.4.1 reads Simcenter 2512 OP2s with a harmless warning
("nx version='2512' is not supported"); every table the checks need loads.

All vectors handed to the checks are in the BASIC coordinate system: OP2
displacements come in each grid's output system (CD), so they are transformed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from pyNastran.bdf.bdf import BDF
from pyNastran.op2.op2 import OP2


def load_op2(path) -> OP2:
    op2 = OP2(debug=False, mode="nx")
    op2.read_op2(str(path), build_dataframe=False)
    return op2


# --------------------------------------------------------------- displacements
@dataclass
class Field:
    nids: np.ndarray    # (n,) grid ids (GRIDs only, SPOINTs dropped)
    xyz: np.ndarray     # (n, 3) basic coordinates of those grids
    t: np.ndarray       # (n, 3) translations in basic
    r: np.ndarray       # (n, 3) rotations in basic


def cd_axes(coord, xyz: np.ndarray) -> np.ndarray:
    """Unit vectors (in basic) of output system ``coord`` at basic positions ``xyz``.

    Returns (n, 3, 3): axes[k, a] is local axis a at grid k. A rectangular system has the
    same axes everywhere; a cylindrical system's (r, theta, z) and a spherical system's
    (r, theta, phi) axes depend on the grid's position (Nastran's convention: theta about
    the local z axis for C; theta from z and phi about z for S). A grid on the axis gets
    theta = 0, as in Nastran."""
    i, j, k = (np.asarray(coord.i, float), np.asarray(coord.j, float), np.asarray(coord.k, float))
    n = len(xyz)
    if coord.Type == "R":
        return np.broadcast_to(np.stack([i, j, k]), (n, 3, 3)).copy()
    p = (xyz - np.asarray(coord.origin, float)) @ np.stack([i, j, k]).T  # local rectangular coords
    phi = np.arctan2(p[:, 1], p[:, 0])                                  # angle about local z
    c, s_ = np.cos(phi)[:, None], np.sin(phi)[:, None]
    if coord.Type == "C":
        e_r = c * i + s_ * j
        e_t = -s_ * i + c * j
        return np.stack([e_r, e_t, np.broadcast_to(k, (n, 3))], axis=1)
    if coord.Type == "S":
        th = np.arctan2(np.hypot(p[:, 0], p[:, 1]), p[:, 2])[:, None]    # angle from local z
        ct, st = np.cos(th), np.sin(th)
        e_r = st * (c * i + s_ * j) + ct * k
        e_t = ct * (c * i + s_ * j) - st * k
        e_p = -s_ * i + c * j
        return np.stack([e_r, e_t, e_p], axis=1)
    raise ValueError(f"coordinate system {coord.cid} has unsupported type {coord.Type!r}")


def displacements_basic(op2: OP2, model: BDF, transformed: Optional[dict] = None) -> dict[int, Field]:
    """{subcase: Field} with translations and rotations in the basic system.

    OP2 displacements are in each grid's output system (CD). They are rotated to basic
    here, matched by GRID ID. (pyNastran's own transform_displacements_to_global indexes
    by position in the model's node list and is documented to work only when every node
    is present in the same order; extra points, e.g. Lagrange multiplier points under
    RIGID=LAGRAN, or scalar points, would misalign it.) Rows that are not model GRIDs are
    dropped."""
    out = {}
    for sc, disp in op2.displacements.items():
        ngt = np.asarray(disp.node_gridtype)
        is_grid = ngt[:, 1] == 1  # 1 = GRID; scalar/extra/Lagrange points have other types
        nids = ngt[is_grid, 0]
        keep = np.array([int(n) in model.nodes for n in nids], dtype=bool)
        nids = nids[keep]
        data = np.asarray(disp.data[0], dtype=float)[is_grid][keep]
        xyz = np.array([model.nodes[int(n)].get_position() for n in nids], dtype=float).reshape(-1, 3)
        t, r = data[:, :3].copy(), data[:, 3:6].copy()
        cds = np.array([model.nodes[int(n)].cd or 0 for n in nids], dtype=int)
        for cd in np.unique(cds):
            if cd == 0:
                continue
            m = cds == cd
            ax = cd_axes(model.coords[int(cd)], xyz[m])            # (n, 3, 3)
            t[m] = np.einsum("na,nab->nb", t[m], ax)
            r[m] = np.einsum("na,nab->nb", r[m], ax)
        out[sc] = Field(nids, xyz, t, r)
    return out


def rigid_body_field(xyz: np.ndarray, mount_xyz: np.ndarray, dof: int, axes=None):
    """Expected motion for a unit SPCD at the mount in DOF 1..6, in basic.

    The SPCD acts along the mount grid's output (CD) axes; ``axes`` holds those axes as
    basic unit vectors (rows), identity when CD = 0.
    Translation DOF k: every grid moves e_k, no rotation.
    Rotation DOF k: every grid rotates theta = e_k and moves theta x (r - r_mount).
    Linear statics uses the small-rotation form, so this is exact for a 1 rad SPCD."""
    n = xyz.shape[0]
    ax = np.eye(3) if axes is None else np.asarray(axes, dtype=float)
    e = ax[(dof - 1) % 3]
    if dof <= 3:
        return np.tile(e, (n, 1)), np.zeros((n, 3))
    return np.cross(e, xyz - mount_xyz), np.tile(e, (n, 1))


def thermal_field(xyz: np.ndarray, mount_xyz: np.ndarray, alpha: float, dT: float):
    """Free expansion from a 123456 mount with uniform alpha: u = alpha*dT*(r - r_m), no rotation.
    This replaces the Book's THDEF program (femci ~2285)."""
    return alpha * dT * (xyz - mount_xyz), np.zeros_like(xyz)


def field_error(f: Field, t_exp: np.ndarray, r_exp: np.ndarray,
                rotation_grids: Optional[set] = None, top: int = 10) -> dict:
    """Relative error of a displacement field against the expected one.

    translation error = max |t - t_exp| / max |t_exp|   (all grids)
    rotation error    = max |r - r_exp| / max(|r_exp|, scale) on grids that have
                        rotational stiffness (solid-only grids are AUTOSPC'd in rotation
                        and would read 0 even though the body rotated)."""
    dt = np.linalg.norm(f.t - t_exp, axis=1)
    scale_t = float(np.linalg.norm(t_exp, axis=1).max()) or 1.0
    res = dict(t_err=float(dt.max() / scale_t), t_scale=scale_t,
               worst_t=[(int(f.nids[i]), float(dt[i] / scale_t))
                        for i in np.argsort(dt)[::-1][:top]])
    if rotation_grids is not None:
        mask = np.array([n in rotation_grids for n in f.nids], dtype=bool)
    else:
        mask = np.ones(len(f.nids), dtype=bool)
    if mask.any():
        dr = np.linalg.norm(f.r[mask] - r_exp[mask], axis=1)
        rexp = float(np.linalg.norm(r_exp[mask], axis=1).max())
        # Thermal expects zero rotation; normalize by translation scale / model size then.
        scale_r = rexp if rexp > 0 else 1.0
        res.update(r_err=float(dr.max() / scale_r), r_scale=scale_r, n_rot_grids=int(mask.sum()),
                   worst_r=[(int(f.nids[mask][i]), float(dr[i] / scale_r))
                            for i in np.argsort(dr)[::-1][:top]])
    else:
        res.update(r_err=float("nan"), r_scale=float("nan"), n_rot_grids=0, worst_r=[])
    return res


# -------------------------------------------------------------- element forces
_EXCLUDE_COLS = {"sd", "station", "xxb"}
_PLATE_FORCE = {"mx", "my", "mxy", "tx", "ty"}   # membrane force/length, transverse shear/length
_PLATE_MOMENT = {"bmx", "bmy", "bmxy"}           # bending moment/length


def _classify(table, header: str) -> Optional[str]:
    h = header.lower()
    if h in _EXCLUDE_COLS:
        return None
    if "Plate" in type(table).__name__:      # CQUAD4/CTRIA3 force: 'mx' is membrane FORCE
        if h in _PLATE_FORCE:
            return "force"
        if h in _PLATE_MOMENT:
            return "moment"
        return None
    if "moment" in h or "torque" in h or h in ("mx", "my", "mz"):
        return "moment"
    return "force"


def element_force_peaks(op2: OP2, subcase: int, top: int = 10) -> dict:
    """Per element type: max |force| and |moment| over all elements.

    Shells requested with FORCE(CORNER) have centroid rows (node id 0) and corner rows.
    The centroid rows are the element result; corner rows are bilinear extrapolations.
    On the reference model the corner rows show 20-80 lbf/in under a 1 rad rigid rotation
    while the centroid rows are ~1e-7, so the pass/fail uses centroid rows and the
    corner peak is reported separately (key 'corner_force')."""
    out = {}
    force_res = op2.op2_results.force
    for name in sorted(dir(force_res)):
        if not name.endswith("_force"):
            continue
        tables = getattr(force_res, name, None)
        if not isinstance(tables, dict) or subcase not in tables:
            continue
        t = tables[subcase]
        try:
            headers = t.get_headers()
        except Exception:
            continue
        data = np.abs(np.asarray(t.data[0], dtype=float))
        is_plate = "Plate" in type(t).__name__
        if hasattr(t, "element_node"):
            en = np.asarray(t.element_node)
            eids = en[:, 0]
            # Plate CORNER output: node id 0 = centroid row, others = corner rows.
            # Beams also have element_node, but their rows are end stations (all real).
            centroid = (en[:, 1] == 0) if is_plate else np.ones(len(eids), dtype=bool)
        else:
            eids = np.asarray(t.element)
            centroid = np.ones(len(eids), dtype=bool)
        kinds = [_classify(t, h) for h in headers]
        fcols = [i for i, k in enumerate(kinds) if k == "force"]
        mcols = [i for i, k in enumerate(kinds) if k == "moment"]
        rec = dict(etype=name[:-6].upper(), n=int(len(np.unique(eids))))
        for key, cols in (("force", fcols), ("moment", mcols)):
            if not cols:
                rec[key], rec[f"worst_{key}"] = 0.0, []
                continue
            v = data[:, cols].max(axis=1)
            vc = np.where(centroid, v, 0.0)
            i_sorted = np.argsort(vc)[::-1]
            seen, worst = set(), []
            for i in i_sorted:
                if eids[i] in seen:
                    continue
                seen.add(eids[i])
                worst.append((int(eids[i]), float(vc[i])))
                if len(worst) >= top:
                    break
            rec[key] = float(vc.max()) if vc.size else 0.0
            rec[f"worst_{key}"] = worst
            if (~centroid).any():
                vcor = np.where(~centroid, v, 0.0)
                rec[f"corner_{key}"] = float(vcor.max())
                order = np.argsort(vcor)[::-1]
                cw, seen = [], set()
                for i in order:
                    if eids[i] in seen:
                        continue
                    seen.add(eids[i])
                    cw.append((int(eids[i]), float(vcor[i])))
                    if len(cw) >= top:
                        break
                rec[f"worst_corner_{key}"] = cw
        rec["per_length"] = is_plate
        out[rec["etype"]] = rec
    return out


# -------------------------------------------------------------------- stresses
def element_stress_peaks(op2: OP2, subcase: int) -> list[tuple[int, str, float]]:
    """[(eid, type, peak)] for every element with stress output.

    peak = von Mises when the table has it (shells, solids), otherwise max |stress|
    over the stress columns (bars/beams/rods), ignoring margin-of-safety columns."""
    out: dict[int, tuple[str, float]] = {}
    stress_res = op2.op2_results.stress
    for name in sorted(dir(stress_res)):
        if not name.endswith("_stress") or "composite" in name:
            continue
        tables = getattr(stress_res, name, None)
        if not isinstance(tables, dict) or subcase not in tables:
            continue
        t = tables[subcase]
        try:
            headers = [h.lower() for h in t.get_headers()]
        except Exception:
            continue
        data = np.asarray(t.data[0], dtype=float)
        if "von_mises" in headers:
            v = np.abs(data[:, headers.index("von_mises")])
        else:
            cols = [i for i, h in enumerate(headers)
                    if not h.startswith("ms") and "margin" not in h and h not in ("fiber_distance", "omax", "omin")]
            if not cols:
                continue
            v = np.abs(data[:, cols]).max(axis=1)
        if hasattr(t, "element_node"):
            eids = np.asarray(t.element_node)[:, 0]
        elif hasattr(t, "element_layer"):
            eids = np.asarray(t.element_layer)[:, 0]
        else:
            eids = np.asarray(t.element)
        etype = name[:-7].upper()
        for e, val in zip(eids, v):
            e = int(e)
            if e not in out or val > out[e][1]:
                out[e] = (etype, float(val))
    return sorted(((e, t, v) for e, (t, v) in out.items()), key=lambda x: -x[2])


def connector_neighbors(model: BDF) -> dict[int, list[str]]:
    """grid -> ['RBE2 400', 'CBUSH 600', ...] for rigid elements, springs and bushes.
    Used to annotate thermal hotspots with what they touch."""
    out: dict[int, list[str]] = {}
    for e in model.elements.values():
        if e.type in ("CBUSH", "CELAS1", "CELAS2", "CELAS3", "CELAS4", "CBUSH1D", "CGAP"):
            for n in e.node_ids:
                if n:
                    out.setdefault(n, []).append(f"{e.type} {e.eid}")
    for r in model.rigid_elements.values():
        try:
            nids = r.independent_nodes + r.dependent_nodes
        except Exception:
            continue
        for n in nids:
            out.setdefault(n, []).append(f"{r.type} {r.eid}")
    return out
