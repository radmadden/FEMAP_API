"""
==============================================================================
 npz_to_3D.py  --  thermal npz  ->  vertex-centered circular 3D point cloud
==============================================================================

PURPOSE
    Turn a 2D (x,z) transient thermal result into a dense 3D (x, y, z, T) point
    cloud for one circular mirror at one time, written as a table that FEMAP can
    ingest as an "arbitrary 3D scalar map" (point cloud + scalar value).

THE GEOMETRY / MATH  (read this before changing anything below)

  1. THERMAL FIELD
     The npz stores T as [timestep, node].  The node map lists every node as a
     cell centroid (x_m, z_m) with cell size (dx_m, dz_m), grouped by plate:
         x = in-plane direction (uniform dx across the plate width)
         z = through-thickness (geometric spacing, dense near the surface)
     The field varies with x and z and is UNIFORM in y.

  2. TEMPERATURE MAPPING  (by name, not by index)
     T columns are aligned to the npz 'node_names' array.  We map each node map
     row to its column by name:  temp = T[timestep, name_to_col[node_name]].
     (The order happens to match node_id today, but name lookup stays correct
     even if that ever changes.)

  3. VERTEX-CENTERED CIRCULAR FACE
     Each mirror is a physical disk whose DIAMETER L is given per plate below;
     radius R = L / 2.  The coordinate origin is the mirror VERTEX (center of
     the optical face) = (0, 0, 0).  The thermal mesh only describes the SHAPE
     of the field along x, so we rescale mesh x onto the true length and center
     it on the vertex:
         X_mesh = ncols * dx                      (mesh spans 0 .. X_mesh)
         x_c    = (x_m / X_mesh) * L  -  R         (x_c runs about -R .. +R)
     Because T is uniform in y, the disk is filled by sweeping y: a grid point
     (x, y) belongs to the mirror when  x^2 + y^2 <= R^2,  and it takes T from
     the x-profile at that x (independent of y).
     z = 0 at the front surface, increasing into the thickness.

  4. DENSE RESAMPLING
     Only a few x-nodes and ~10 z-nodes exist, so we resample onto a regular
     in-plane grid at GRID_SPACING_IN and LINEARLY interpolate T along x (held
     flat past the outermost nodes out to the rim).  z levels are the thermal
     z-nodes plus a surface (z=0) and back-face (z=thickness) point.

  5. OUTPUT
     Four columns  x, y, z, T  (inches / model temperature units), one point per
     row.  Nothing is written unless every validation below passes.

NOTE: the import_point_cloud.bas macro is a separate visualization aid and is
      NOT the consumer of this file; do not shape the output to it.
==============================================================================
"""

import os
import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# USER SETTINGS
# ---------------------------------------------------------------------------
METERS_TO_INCHES = 39.37007874

GRID_SPACING_IN = 0.25     # in-plane point spacing of the dense cloud (smaller = denser)
ADD_Z_EDGE_POINTS = True   # add z=0 surface and z=thickness back-face points
WRITE_HEADER = True        # write the "x,y,z,T" header row; False if FEMAP wants none

# Physical DIAMETER of each mirror, in INCHES (radius R = L/2).
# x and y both span [-R, R]; the face is clipped to the circle of radius R.
# TODO: replace the provisional M4-M7 value once measured.
MIRROR_DIAMETER_IN = {
    "M1": 15.75,
    "M2": 3.46,    # NOTE: smaller than its thermal mesh span (~6.225) -> see discrepancy warning
    "M3": 4.65,
    "M4": 5.905,   # M4-M7 share one value (provisional)
    "M5": 5.905,
    "M6": 5.905,
    "M7": 5.905,
}
# W1, P1, Mel, Maz are NOT mirrors -> not supported (rejected if selected).

EPS = 1e-9


class PipelineError(Exception):
    """Raised for any validation failure; shown to the user and aborts without writing."""


# ===========================================================================
# CORE PIPELINE (pure logic; no dialogs) -- kept separate so it is testable
# ===========================================================================
def build_cloud(df_nodes, t_array, T_matrix, node_names, target_component,
                target_time, grid_spacing=GRID_SPACING_IN):
    """
    Return (cloud, info) where cloud is an (N,4) float array of x,y,z,T and info
    is a dict of summary/diagnostic values. Raises PipelineError on bad input.
    """
    info = {}

    # --- validate node map columns -----------------------------------------
    required = ["node_id", "node_name", "plate", "i_x", "j_z",
                "x_m", "z_m", "dx_m", "dz_m"]
    missing = [c for c in required if c not in df_nodes.columns]
    if missing:
        raise PipelineError("Node map is missing column(s): " + ", ".join(missing))

    # --- validate npz arrays ------------------------------------------------
    n_t = len(t_array)
    if T_matrix.ndim != 2:
        raise PipelineError("npz 'T' must be 2D [timestep, node]; got shape %s" % (T_matrix.shape,))
    if T_matrix.shape[0] != n_t:
        raise PipelineError("npz 'T' rows (%d) != len(t) (%d)." % (T_matrix.shape[0], n_t))
    if T_matrix.shape[1] != len(node_names):
        raise PipelineError("npz 'T' cols (%d) != len(node_names) (%d)."
                            % (T_matrix.shape[1], len(node_names)))

    # --- mirror only --------------------------------------------------------
    target_component = str(target_component).strip()
    if target_component not in MIRROR_DIAMETER_IN:
        raise PipelineError(
            "'%s' is not a supported mirror.\nSupported: %s\n"
            "(W1/P1/Mel/Maz are not mirrors.)"
            % (target_component, ", ".join(sorted(MIRROR_DIAMETER_IN))))
    L = float(MIRROR_DIAMETER_IN[target_component])
    if L <= 0:
        raise PipelineError("Diameter for %s must be positive." % target_component)
    R = L / 2.0

    # --- grid spacing sanity ------------------------------------------------
    if not (0 < grid_spacing < R):
        raise PipelineError("GRID_SPACING_IN (%.4f) must be > 0 and < R (%.4f)."
                            % (grid_spacing, R))

    # --- name -> column map, then isolate the plate -------------------------
    name_to_col = {str(n).strip(): i for i, n in enumerate(node_names)}

    df = df_nodes.copy()
    df["plate"] = df["plate"].astype(str).str.strip()
    df = df[df["plate"] == target_component].copy()
    if df.empty:
        raise PipelineError("No nodes for plate '%s' in the node map." % target_component)

    # all names resolvable?
    df["node_name"] = df["node_name"].astype(str).str.strip()
    missing_names = [nm for nm in df["node_name"] if nm not in name_to_col]
    if missing_names:
        raise PipelineError("%d node name(s) not found in npz node_names, e.g. %s"
                            % (len(missing_names), ", ".join(missing_names[:5])))

    # --- resolve timestep ---------------------------------------------------
    timestep_idx = int(np.abs(t_array - target_time).argmin())
    actual_time = float(t_array[timestep_idx])
    T_snapshot = T_matrix[timestep_idx, :]

    # --- x is uniform in dx (rescale assumes it) ----------------------------
    dx_vals = np.unique(np.round(df["dx_m"].to_numpy(dtype=float), 12))
    if dx_vals.size != 1:
        raise PipelineError("Plate %s has non-uniform dx (%s); rescale assumes uniform x."
                            % (target_component, dx_vals))
    dx_m = float(dx_vals[0])

    # columns (i_x) and rows (j_z)
    ix_sorted = sorted(df["i_x"].unique(), key=lambda v: int(v))
    jz_sorted = sorted(df["j_z"].unique(), key=lambda v: int(v))
    ncols = len(ix_sorted)
    X_mesh_m = ncols * dx_m
    if X_mesh_m <= 0:
        raise PipelineError("Degenerate mesh x span for plate %s." % target_component)

    # centered, rescaled x position per i_x column (inches)
    xc_by_ix = {}
    for ix in ix_sorted:
        x_m = float(df[df["i_x"] == ix]["x_m"].iloc[0])
        xc_by_ix[ix] = (x_m / X_mesh_m) * L - R
    x_nodes_c = np.array([xc_by_ix[ix] for ix in ix_sorted], dtype=float)

    # z position per j_z (inches) and cumulative thickness
    zin_by_jz = {}
    for jz in jz_sorted:
        z_m = float(df[df["j_z"] == jz]["z_m"].iloc[0])
        zin_by_jz[jz] = z_m * METERS_TO_INCHES
    # full thickness = sum of dz over a column
    one_col = df[df["i_x"] == ix_sorted[0]].sort_values("j_z")
    thickness_in = float(one_col["dz_m"].astype(float).sum()) * METERS_TO_INCHES

    # --- T(x, z) table: rows=x node (ncols), cols=z level -------------------
    # base z levels are the thermal nodes; optionally bracket with surface/back
    z_levels = [zin_by_jz[jz] for jz in jz_sorted]
    # temp lookup helper for a (ix, jz)
    def temp_at(ix, jz):
        row = df[(df["i_x"] == ix) & (df["j_z"] == jz)]
        nm = row["node_name"].iloc[0]
        return float(T_snapshot[name_to_col[nm]])

    T_xz = np.empty((ncols, len(jz_sorted)), dtype=float)
    for ci, ix in enumerate(ix_sorted):
        for zi, jz in enumerate(jz_sorted):
            T_xz[ci, zi] = temp_at(ix, jz)

    if ADD_Z_EDGE_POINTS:
        # prepend surface z=0 (nearest node = first jz), append back face
        z_levels = [0.0] + z_levels + [thickness_in]
        T_xz = np.column_stack([T_xz[:, [0]], T_xz, T_xz[:, [-1]]])
    z_levels = np.array(z_levels, dtype=float)
    nz = z_levels.size

    # --- field sanity: no NaN/inf in the mapped temps -----------------------
    if not np.all(np.isfinite(T_xz)):
        raise PipelineError("Mapped temperatures contain NaN/inf for %s at t=%.3fs."
                            % (target_component, actual_time))

    # --- dense in-plane grid, clipped to the disk ---------------------------
    # symmetric grid centered on the vertex
    n_half = int(np.floor(R / grid_spacing))
    axis = np.arange(-n_half, n_half + 1) * grid_spacing
    X, Y = np.meshgrid(axis, axis, indexing="xy")
    mask = (X * X + Y * Y) <= (R * R + EPS)
    if not np.any(mask):
        raise PipelineError("Grid produced no points inside the disk; spacing too coarse.")
    xk = X[mask]
    yk = Y[mask]

    # interpolate T along x for every z level (flat-held past the end nodes)
    # np.interp requires ascending xp; x_nodes_c is ascending by construction
    T_on_x = np.vstack([np.interp(xk, x_nodes_c, T_xz[:, zi]) for zi in range(nz)])  # (nz, Nkept)

    # assemble the cloud: every kept (x,y) at every z level
    blocks = []
    for zi in range(nz):
        zcol = np.full(xk.shape, z_levels[zi])
        blocks.append(np.column_stack([xk, yk, zcol, T_on_x[zi]]))
    cloud = np.vstack(blocks)

    # --- de-duplicate coincident (x,y,z) ------------------------------------
    keys = np.round(cloud[:, :3], 8)
    _, uniq_idx = np.unique(keys, axis=0, return_index=True)
    if uniq_idx.size != cloud.shape[0]:
        cloud = cloud[np.sort(uniq_idx)]

    # --- output validation --------------------------------------------------
    if cloud.shape[0] == 0:
        raise PipelineError("No output points generated.")
    if not np.all(np.isfinite(cloud)):
        raise PipelineError("Output contains NaN/inf values.")
    rr = cloud[:, 0] ** 2 + cloud[:, 1] ** 2
    if np.max(rr) > R * R + 1e-6:
        raise PipelineError("Some points fall outside the mirror disk (clip failed).")
    if cloud[:, 2].min() < -1e-6 or cloud[:, 2].max() > thickness_in + 1e-6:
        raise PipelineError("z values out of [0, thickness] range.")
    # centered on the vertex?
    if abs(cloud[:, 0].min() + cloud[:, 0].max()) > grid_spacing + 1e-6:
        raise PipelineError("x not centered on the vertex (origin check failed).")

    info.update(dict(
        plate=target_component, L=L, R=R,
        mesh_span_in=X_mesh_m * METERS_TO_INCHES,
        dx_in=dx_m * METERS_TO_INCHES,
        ncols=ncols, n_zlevels=nz, thickness_in=thickness_in,
        timestep_idx=timestep_idx, actual_time=actual_time,
        grid_spacing=grid_spacing, n_points=cloud.shape[0],
        x_range=(float(cloud[:, 0].min()), float(cloud[:, 0].max())),
        y_range=(float(cloud[:, 1].min()), float(cloud[:, 1].max())),
        z_range=(float(cloud[:, 2].min()), float(cloud[:, 2].max())),
        T_range=(float(cloud[:, 3].min()), float(cloud[:, 3].max())),
    ))
    # discrepancy flag: mesh span vs physical diameter
    info["span_mismatch"] = abs(info["mesh_span_in"] - L) > 0.02 * L
    return cloud, info


def summary_text(info, output_path=None):
    lines = [
        "Mirror:        %s" % info["plate"],
        "Diameter L:    %.4f in   (R = %.4f in)" % (info["L"], info["R"]),
        "Mesh x-span:   %.4f in   (dx = %.4f in, %d cols)" % (
            info["mesh_span_in"], info["dx_in"], info["ncols"]),
    ]
    if info["span_mismatch"]:
        lines.append("  ** mesh span != diameter by >2% -- thermal/CAD discrepancy, rescaled **")
    lines += [
        "Timestep:      idx %d  ->  t = %.3f s" % (info["timestep_idx"], info["actual_time"]),
        "Thickness:     %.5f in   (%d z-levels)" % (info["thickness_in"], info["n_zlevels"]),
        "Grid spacing:  %.4f in" % info["grid_spacing"],
        "Points:        %d" % info["n_points"],
        "x range:       %.4f .. %.4f in" % info["x_range"],
        "y range:       %.4f .. %.4f in" % info["y_range"],
        "z range:       %.5f .. %.5f in" % info["z_range"],
        "T range:       %.4f .. %.4f" % info["T_range"],
    ]
    if output_path:
        lines.append("Saved:         %s" % output_path)
    return "\n".join(lines)


# ===========================================================================
# INTERACTIVE DRIVER (dialogs + file IO)
# ===========================================================================
def main():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    # A. node map
    print("Opening file explorer for the node map...")
    node_map_path = filedialog.askopenfilename(
        title="STEP 1: Select your Node Layout Reference Map File",
        filetypes=[("Text/CSV Files", "*.txt;*.csv"), ("All Files", "*.*")])
    if not node_map_path:
        print("Canceled: no node map.")
        return

    # B. npz
    print("Opening file explorer for the temperature dataset...")
    npz_path = filedialog.askopenfilename(
        title="STEP 2: Select your Temperature (.npz) File",
        filetypes=[("NumPy Zip Files", "*.npz"), ("All Files", "*.*")])
    if not npz_path:
        print("Canceled: no npz.")
        return

    # load
    data = None
    try:
        data = np.load(npz_path, allow_pickle=True)
        for key in ("t", "T", "node_names"):
            if key not in data.files:
                raise PipelineError("npz is missing required array '%s'." % key)
        t_array = data["t"]
        T_matrix = data["T"]
        node_names = data["node_names"]
        df_nodes = pd.read_csv(node_map_path)

        # which mirrors are actually present in the node map?
        present = set(df_nodes["plate"].astype(str).str.strip().unique())
        mirrors_here = sorted(m for m in MIRROR_DIAMETER_IN if m in present)
        if not mirrors_here:
            raise PipelineError("No supported mirrors (M1-M7) found in the node map.")

        # time prompt
        target_time = simpledialog.askinteger(
            "Transient Snapshot",
            "Enter the target timestamp in seconds (%d .. %d):"
            % (int(t_array.min()), int(t_array.max())),
            minvalue=int(t_array.min()), maxvalue=int(t_array.max()))
        if target_time is None:
            return

        # mirror prompt
        target_component = simpledialog.askstring(
            "Mirror Selector",
            "Mirrors in this model:\n%s\n\nType the mirror name (e.g. M1):"
            % ", ".join(mirrors_here))
        if not target_component:
            return

        # output folder
        output_folder = filedialog.askdirectory(
            title="STEP 3: Select target folder to save the point cloud")
        if not output_folder:
            return

        # build + validate (everything that can fail is in here)
        cloud, info = build_cloud(df_nodes, t_array, T_matrix, node_names,
                                  target_component, target_time)

        # write
        out_name = "%s_MirrorCloud3D_%ds.csv" % (info["plate"], int(info["actual_time"]))
        output_path = os.path.join(output_folder, out_name)
        df_out = pd.DataFrame(cloud, columns=["x", "y", "z", "T"])
        df_out.to_csv(output_path, index=False, header=WRITE_HEADER, float_format="%.8f")

        msg = summary_text(info, output_path)
        print("\n" + msg)
        if info["span_mismatch"]:
            print("WARNING: mesh span != diameter (>2%); see summary.")
        messagebox.showinfo("Point cloud complete", msg)

    except PipelineError as e:
        print("ERROR: %s" % e)
        messagebox.showerror("Aborted -- no file written", str(e))
    except Exception as e:  # unexpected: still fail loud, write nothing
        print("UNEXPECTED ERROR: %s" % e)
        messagebox.showerror("Aborted -- no file written", "Unexpected error:\n%s" % e)
    finally:
        if data is not None:
            data.close()


if __name__ == "__main__":
    main()
