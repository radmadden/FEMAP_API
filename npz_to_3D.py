"""
==============================================================================
 npz_to_3D.py  --  thermal npz  ->  vertex-centered circular 3D point cloud
==============================================================================

PURPOSE
    Turn a 2D (x,z) transient thermal result into a 3D (x, y, z, T) point cloud
    for one circular mirror at one time, written as a table FEMAP can ingest as
    an "arbitrary 3D scalar map" (point cloud + scalar value).

    Two output flavors share the SAME geometry/temperature model:
      * DENSE   analysis cloud  -> _MirrorCloud3D_<t>s.csv   (regular grid)
      * SKELETON viz cloud      -> _VizSkeleton_<t>s.csv      (rim + a few rows/
                                   cols; a few hundred points, plots instantly)

THE GEOMETRY / MATH  (read this before changing anything below)

  1. THERMAL FIELD
     The npz stores T as [timestep, node].  Each node (node map) is a cell
     centroid (x_m, z_m), cell size (dx_m, dz_m), grouped by plate:
         x = in-plane direction (uniform dx across the plate width)
         z = through-thickness (geometric spacing, dense near the surface)
     The field varies with x and z and is UNIFORM in y.

  2. TEMPERATURE MAPPING  (by name, not by index)
     temp = T[timestep, name_to_col[node_name]], using the npz 'node_names'.

  3. VERTEX-CENTERED CIRCULAR FACE
     Mirror DIAMETER L is per plate below; radius R = L/2.  Origin = mirror
     VERTEX (center of the front face) = (0,0,0).  Mesh x is rescaled onto the
     true length and centered:
         X_mesh = ncols * dx
         x_c    = (x_m / X_mesh) * L  -  R        (x_c runs about -R .. +R)
     T is uniform in y, so a point (x,y) with x^2+y^2 <= R^2 takes T from the
     x-profile at that x.  z = 0 at the front surface, into the thickness.

  4. RESAMPLING
     DENSE:    regular grid at GRID_SPACING_IN, clipped to the disk.
     SKELETON: the outer rim ring + a few full-width rows and columns, on a
               couple of through-thickness planes.
     T is linearly interpolated along x (held flat past the end nodes) and,
     for off-node z planes, along z.

  5. OUTPUT
     Four columns x, y, z, T (inches / model temperature units).  Nothing is
     written unless every validation passes.

NOTE: import_point_cloud.bas / show_point_cloud.bas are separate visualization
      aids and are NOT the consumer of this file.
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

GRID_SPACING_IN = 1.0      # dense cloud: in-plane point spacing (smaller = denser);
                           # face is isothermal so coarse is fine -- ~16x fewer points than 0.25
ADD_Z_EDGE_POINTS = True   # add z=0 surface and z=thickness back-face points
WRITE_HEADER = True        # write the "x,y,z,T" header row; False if FEMAP wants none

# SKELETON (viz) sampling -- a few hundred points for a fast geometry check
SKELETON = dict(
    ring_pts=72,                       # points around the outer rim (every 5 deg)
    n_rows=5,                          # horizontal chords (constant y)
    n_cols=5,                          # vertical chords (constant x)
    pts_per_line=25,                   # samples along each chord
    z_levels=("surface", "back"),      # planes to emit: surface / mid / back
)

# Physical DIAMETER of each mirror, in INCHES (radius R = L/2).
# TODO: replace the provisional M4-M7 value once measured.
MIRROR_DIAMETER_IN = {
    "M1": 15.75,
    "M2": 3.46,    # NOTE: smaller than its thermal mesh span (~6.225) -> discrepancy warning
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
# SHARED FIELD MODEL  (geometry + temperatures for one plate/time)
# ===========================================================================
def _build_field_model(df_nodes, t_array, T_matrix, node_names,
                        target_component, target_time):
    """Validate inputs and build the centered field model for one mirror/time.

    Returns a dict with the centered x nodes, z levels, the T(x,z) table, and
    geometry/diagnostic values. Raises PipelineError on bad input.
    """
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

    # --- name -> column map, then isolate the plate -------------------------
    name_to_col = {str(n).strip(): i for i, n in enumerate(node_names)}

    df = df_nodes.copy()
    df["plate"] = df["plate"].astype(str).str.strip()
    df = df[df["plate"] == target_component].copy()
    if df.empty:
        raise PipelineError("No nodes for plate '%s' in the node map." % target_component)

    df["node_name"] = df["node_name"].astype(str).str.strip()
    missing_names = [nm for nm in df["node_name"] if nm not in name_to_col]
    if missing_names:
        raise PipelineError("%d node name(s) not found in npz node_names, e.g. %s"
                            % (len(missing_names), ", ".join(missing_names[:5])))

    # --- resolve timestep ---------------------------------------------------
    timestep_idx = int(np.abs(t_array - target_time).argmin())
    actual_time = float(t_array[timestep_idx])
    T_snapshot = T_matrix[timestep_idx, :]

    # --- x uniform in dx (rescale assumes it) -------------------------------
    dx_vals = np.unique(np.round(df["dx_m"].to_numpy(dtype=float), 12))
    if dx_vals.size != 1:
        raise PipelineError("Plate %s has non-uniform dx (%s); rescale assumes uniform x."
                            % (target_component, dx_vals))
    dx_m = float(dx_vals[0])

    ix_sorted = sorted(df["i_x"].unique(), key=lambda v: int(v))
    jz_sorted = sorted(df["j_z"].unique(), key=lambda v: int(v))
    ncols = len(ix_sorted)
    X_mesh_m = ncols * dx_m
    if X_mesh_m <= 0:
        raise PipelineError("Degenerate mesh x span for plate %s." % target_component)

    # centered, rescaled x per column (inches)
    x_nodes_c = np.array(
        [(float(df[df["i_x"] == ix]["x_m"].iloc[0]) / X_mesh_m) * L - R for ix in ix_sorted],
        dtype=float)

    # z per row (inches) + full thickness
    z_nodes = np.array([float(df[df["j_z"] == jz]["z_m"].iloc[0]) * METERS_TO_INCHES
                        for jz in jz_sorted], dtype=float)
    one_col = df[df["i_x"] == ix_sorted[0]].sort_values("j_z")
    thickness_in = float(one_col["dz_m"].astype(float).sum()) * METERS_TO_INCHES

    # T(x, z) table: rows = x node, cols = z level
    def temp_at(ix, jz):
        nm = df[(df["i_x"] == ix) & (df["j_z"] == jz)]["node_name"].iloc[0]
        return float(T_snapshot[name_to_col[nm]])

    T_xz = np.empty((ncols, len(jz_sorted)), dtype=float)
    for ci, ix in enumerate(ix_sorted):
        for zi, jz in enumerate(jz_sorted):
            T_xz[ci, zi] = temp_at(ix, jz)

    z_levels = z_nodes
    if ADD_Z_EDGE_POINTS:
        z_levels = np.concatenate([[0.0], z_nodes, [thickness_in]])
        T_xz = np.column_stack([T_xz[:, [0]], T_xz, T_xz[:, [-1]]])

    if not np.all(np.isfinite(T_xz)):
        raise PipelineError("Mapped temperatures contain NaN/inf for %s at t=%.3fs."
                            % (target_component, actual_time))

    mesh_span_in = X_mesh_m * METERS_TO_INCHES
    return dict(
        plate=target_component, L=L, R=R,
        x_nodes_c=x_nodes_c, z_levels=z_levels, T_xz=T_xz,
        ncols=ncols, nz=z_levels.size, thickness_in=thickness_in,
        timestep_idx=timestep_idx, actual_time=actual_time,
        dx_in=dx_m * METERS_TO_INCHES, mesh_span_in=mesh_span_in,
        span_mismatch=abs(mesh_span_in - L) > 0.02 * L,
    )


def _T_at(field, xq, zq):
    """Interpolated temperature at in-plane x position(s) xq on z-plane zq.
    Linear in z across the node levels, then linear in x (flat-held past ends).
    """
    Tz_nodes = np.array([np.interp(zq, field["z_levels"], field["T_xz"][ci, :])
                         for ci in range(field["ncols"])])
    return np.interp(xq, field["x_nodes_c"], Tz_nodes)


def _z_plane_value(field, name):
    return {"surface": 0.0,
            "mid": field["thickness_in"] / 2.0,
            "back": field["thickness_in"]}[name]


def _finalize(cloud, field, detail, center_tol):
    """Shared output validation + info/summary dict. Raises on bad geometry."""
    # de-duplicate coincident (x,y,z)
    keys = np.round(cloud[:, :3], 8)
    _, uniq_idx = np.unique(keys, axis=0, return_index=True)
    if uniq_idx.size != cloud.shape[0]:
        cloud = cloud[np.sort(uniq_idx)]

    if cloud.shape[0] == 0:
        raise PipelineError("No output points generated.")
    if not np.all(np.isfinite(cloud)):
        raise PipelineError("Output contains NaN/inf values.")
    R = field["R"]
    if np.max(cloud[:, 0] ** 2 + cloud[:, 1] ** 2) > R * R + 1e-6:
        raise PipelineError("Some points fall outside the mirror disk (clip failed).")
    if cloud[:, 2].min() < -1e-6 or cloud[:, 2].max() > field["thickness_in"] + 1e-6:
        raise PipelineError("z values out of [0, thickness] range.")
    if abs(cloud[:, 0].min() + cloud[:, 0].max()) > center_tol + 1e-6:
        raise PipelineError("x not centered on the vertex (origin check failed).")

    info = dict(
        plate=field["plate"], L=field["L"], R=R,
        mesh_span_in=field["mesh_span_in"], dx_in=field["dx_in"],
        ncols=field["ncols"], n_zlevels=field["nz"], thickness_in=field["thickness_in"],
        timestep_idx=field["timestep_idx"], actual_time=field["actual_time"],
        span_mismatch=field["span_mismatch"], detail=detail,
        n_points=cloud.shape[0],
        x_range=(float(cloud[:, 0].min()), float(cloud[:, 0].max())),
        y_range=(float(cloud[:, 1].min()), float(cloud[:, 1].max())),
        z_range=(float(cloud[:, 2].min()), float(cloud[:, 2].max())),
        T_range=(float(cloud[:, 3].min()), float(cloud[:, 3].max())),
    )
    return cloud, info


# ===========================================================================
# DENSE analysis cloud
# ===========================================================================
def build_cloud(df_nodes, t_array, T_matrix, node_names, target_component,
                target_time, grid_spacing=GRID_SPACING_IN):
    field = _build_field_model(df_nodes, t_array, T_matrix, node_names,
                               target_component, target_time)
    R = field["R"]
    if not (0 < grid_spacing < R):
        raise PipelineError("GRID_SPACING_IN (%.4f) must be > 0 and < R (%.4f)."
                            % (grid_spacing, R))

    # symmetric in-plane grid centered on the vertex, clipped to the disk
    n_half = int(np.floor(R / grid_spacing))
    axis = np.arange(-n_half, n_half + 1) * grid_spacing
    X, Y = np.meshgrid(axis, axis, indexing="xy")
    mask = (X * X + Y * Y) <= (R * R + EPS)
    if not np.any(mask):
        raise PipelineError("Grid produced no points inside the disk; spacing too coarse.")
    xk, yk = X[mask], Y[mask]

    blocks = []
    for zval in field["z_levels"]:
        Tz = _T_at(field, xk, zval)
        blocks.append(np.column_stack([xk, yk, np.full(xk.shape, zval), Tz]))
    cloud = np.vstack(blocks)

    detail = "Grid spacing:  %.4f in" % grid_spacing
    return _finalize(cloud, field, detail, center_tol=grid_spacing)


# ===========================================================================
# SKELETON viz cloud  (rim + a few rows/cols)
# ===========================================================================
def build_skeleton(df_nodes, t_array, T_matrix, node_names, target_component,
                   target_time, params=SKELETON):
    field = _build_field_model(df_nodes, t_array, T_matrix, node_names,
                               target_component, target_time)
    R = field["R"]
    ring_pts = int(params["ring_pts"])
    n_rows = int(params["n_rows"])
    n_cols = int(params["n_cols"])
    npl = int(params["pts_per_line"])

    # in-plane (x,y) skeleton, built once and reused on every z plane
    xy = []
    # outer rim ring
    if ring_pts > 0:
        th = np.linspace(0.0, 2.0 * np.pi, ring_pts, endpoint=False)
        xy.append(np.column_stack([R * np.cos(th), R * np.sin(th)]))
    # horizontal rows (constant y), interior only (skip zero-length edge chords)
    for y in np.linspace(-R, R, n_rows + 2)[1:-1]:
        xlim = np.sqrt(max(R * R - y * y, 0.0))
        xs = np.linspace(-xlim, xlim, npl)
        xy.append(np.column_stack([xs, np.full(xs.shape, y)]))
    # vertical columns (constant x)
    for x in np.linspace(-R, R, n_cols + 2)[1:-1]:
        ylim = np.sqrt(max(R * R - x * x, 0.0))
        ys = np.linspace(-ylim, ylim, npl)
        xy.append(np.column_stack([np.full(ys.shape, x), ys]))
    xy = np.vstack(xy)
    xk, yk = xy[:, 0], xy[:, 1]

    blocks = []
    for name in params["z_levels"]:
        zval = _z_plane_value(field, name)
        Tz = _T_at(field, xk, zval)
        blocks.append(np.column_stack([xk, yk, np.full(xk.shape, zval), Tz]))
    cloud = np.vstack(blocks)

    detail = ("Skeleton:      ring=%d, rows=%d, cols=%d, pts/line=%d, planes=%s"
              % (ring_pts, n_rows, n_cols, npl, ",".join(params["z_levels"])))
    # ring hits x = +/-R exactly, so centering is tight
    return _finalize(cloud, field, detail, center_tol=1e-6)


# ===========================================================================
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
        info["detail"],
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

        present = set(df_nodes["plate"].astype(str).str.strip().unique())
        mirrors_here = sorted(m for m in MIRROR_DIAMETER_IN if m in present)
        if not mirrors_here:
            raise PipelineError("No supported mirrors (M1-M7) found in the node map.")

        # output type
        out_mode = simpledialog.askinteger(
            "Output type",
            "1 = dense analysis cloud\n2 = viz skeleton (fast)\n3 = both",
            initialvalue=2, minvalue=1, maxvalue=3)
        if out_mode is None:
            return

        # time
        target_time = simpledialog.askinteger(
            "Transient Snapshot",
            "Enter the target timestamp in seconds (%d .. %d):"
            % (int(t_array.min()), int(t_array.max())),
            minvalue=int(t_array.min()), maxvalue=int(t_array.max()))
        if target_time is None:
            return

        # mirror
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

        # dense in-plane spacing (asked at run time). The face is ~isothermal, so a
        # coarse grid is just as accurate and means far fewer points / much faster
        # mapping in FEMAP. Point count scales as ~1/spacing^2.
        dense_spacing = GRID_SPACING_IN
        if out_mode in (1, 3):
            dense_spacing = simpledialog.askfloat(
                "Grid density",
                "In-plane grid spacing in inches (larger = fewer points, faster).\n"
                "The mirror face is ~isothermal, so coarse is fine.\n"
                "0.25 = dense, 0.5 = medium, 1.0 = fast:",
                initialvalue=GRID_SPACING_IN, minvalue=0.01)
            if dense_spacing is None:
                return

        # build (all validation happens inside) -- nothing written on failure
        jobs = []
        if out_mode in (1, 3):
            cloud, info = build_cloud(df_nodes, t_array, T_matrix, node_names,
                                      target_component, target_time, grid_spacing=dense_spacing)
            jobs.append(("MirrorCloud3D", cloud, info))
        if out_mode in (2, 3):
            cloud, info = build_skeleton(df_nodes, t_array, T_matrix, node_names,
                                         target_component, target_time)
            jobs.append(("VizSkeleton", cloud, info))

        # write + report
        blocks = []
        for tag, cloud, info in jobs:
            out_name = "%s_%s_%ds.csv" % (info["plate"], tag, int(info["actual_time"]))
            output_path = os.path.join(output_folder, out_name)
            pd.DataFrame(cloud, columns=["x", "y", "z", "T"]).to_csv(
                output_path, index=False, header=WRITE_HEADER, float_format="%.8f")
            blocks.append(summary_text(info, output_path))
            if info["span_mismatch"]:
                print("WARNING: %s mesh span != diameter (>2%%)." % info["plate"])

        msg = ("\n" + "-" * 40 + "\n").join(blocks)
        print("\n" + msg)
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
