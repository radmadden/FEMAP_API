import os
import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
import numpy as np
import pandas as pd

# Global Unit Transformation Constant (1 Meter = 39.37007874 Inches)
METERS_TO_INCHES = 39.37007874

# ==========================================
# 1. INITIALIZE INTERFACE & PICK FILES
# ==========================================
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)

# A. Select the Node Geometry Mapping File
print("Opening file explorer for the node map...")
node_map_path = filedialog.askopenfilename(
    title="STEP 1: Select your Node Layout Reference Map File", 
    filetypes=[("Text/CSV Files", "*.txt;*.csv"), ("All Files", "*.*")]
)
if not node_map_path: 
    print("Operation canceled. Missing node coordinate parameters.")
    exit()

# B. Select the Temperature (.npz) Output File 
print("Opening file explorer for the temperature dataset...")
npz_path = filedialog.askopenfilename(
    title="STEP 2: Select your Temperature (.npz) File", 
    filetypes=[("NumPy Zip Files", "*.npz"), ("All Files", "*.*")]
)
if not npz_path: 
    print("Operation canceled. Missing scalar dataset matrix.")
    exit()

# ==========================================
# 2. LOAD DATA & USER SNAPSHOT SELECTION
# ==========================================
print("\nIngesting datasets and resolving structural plates...")
data = np.load(npz_path, allow_pickle=True)
t_array = data['t']  
T_matrix = data['T']  

df_nodes = pd.read_csv(node_map_path)
available_components = df_nodes['plate'].unique()
components_list_str = ", ".join(sorted([str(c) for c in available_components]))

# User Parameter Prompts
target_time = simpledialog.askinteger(
    "Transient Snapshot", "Enter the target timestamp in seconds (e.g., 120):", 
    minvalue=int(t_array.min()), maxvalue=int(t_array.max())
)
if target_time is None:
    data.close()
    exit()

target_component = simpledialog.askstring(
    "Structural Selector", f"Detected Components:\n{components_list_str}\n\nType target mirror name (e.g., M1):"
)
if not target_component:
    data.close()
    exit()

target_component = target_component.strip()
timestep_idx = (np.abs(t_array - target_time)).argmin()
actual_time = t_array[timestep_idx]

# Isolate just our specific component rows
df_filtered = df_nodes[df_nodes['plate'].str.strip() == target_component].copy()

if df_filtered.empty:
    messagebox.showerror("Error", f"Component Label '{target_component}' not recognized inside reference layout file.")
    data.close()
    exit()

print("Opening directory picker to save output spreadsheet...")
output_folder = filedialog.askdirectory(title="STEP 3: Select target folder to dump output file")
if not output_folder: 
    data.close()
    exit()

# Isolate temperature data for the target time frame
T_snapshot = T_matrix[timestep_idx, :]

# ==========================================
# 3. CONVERT UNITS & PREPARE DATAFRAME
# ==========================================
femap_rows = []
df_filtered = df_filtered.sort_values(by=['i_x', 'j_z'])

for _, row in df_filtered.iterrows():
    node_id = int(row['node_id'])
    
    # Transform raw meters centroids directly into target inches mesh
    x_inch = float(row['x_m']) * METERS_TO_INCHES
    y_inch = x_inch  # Map structural cross section directly to Y=X plane symmetry 
    z_inch = float(row['z_m']) * METERS_TO_INCHES
    
    dx_inch = float(row['dx_m']) * METERS_TO_INCHES
    dz_inch = float(row['dz_m']) * METERS_TO_INCHES
    
    temp_val = float(T_snapshot[node_id])
    
    femap_rows.append({
        'id': node_id, 'x': x_inch, 'y': y_inch, 'z': z_inch,
        'dx': dx_inch, 'dz': dz_inch, 'temp': temp_val,
        'ix': int(row['i_x']), 'jz': int(row['j_z'])
    })

df_out = pd.DataFrame(femap_rows)
final_femap_grid = []

# ==========================================
# 4. RESOLVE LAYER Z BOUNDARIES PER COLUMN
# ==========================================
for ix in df_out['ix'].unique():
    col_data = df_out[df_out['ix'] == ix].sort_values(by='jz')
    
    # True surface origin (Air Face) starts at Z = 0.00 inches
    current_thickness_edge = 0.0  
    
    # FIX: Use .loc with explicit indexing to grab the first row item safely
    first_z_row = col_data.loc[col_data.index[0]]
    final_femap_grid.append([
        first_z_row['x'], first_z_row['y'], current_thickness_edge, first_z_row['temp']
    ])
    
    # Dump regular internal element centroids
    for _, row in col_data.iterrows():
        final_femap_grid.append([row['x'], row['y'], row['z'], row['temp']])
        # Track edge position sequentially from cumulative thicknesses to handle geometric stretching
        current_thickness_edge += row['dz']
        
    # FIX: Use .loc with explicit indexing to grab the final row item safely
    last_z_row = col_data.loc[col_data.index[-1]]
    final_femap_grid.append([
        last_z_row['x'], last_z_row['y'], current_thickness_edge, last_z_row['temp']
    ])

# Convert back to DataFrame to step through long face projection (X/Y Outer Bounds)
df_final = pd.DataFrame(final_femap_grid, columns=['X', 'Y', 'Z', 'Temperature'])

# ==========================================
# 5. RESOLVE FULL SURFACE FRAME OUTLINES (X/Y)
# ==========================================
# FIX: Use explicit indexing to extract the bounding column limits safely
first_column_x = df_out[df_out['ix'] == df_out['ix'].min()].loc[df_out[df_out['ix'] == df_out['ix'].min()].index[0]]
last_column_x = df_out[df_out['ix'] == df_out['ix'].max()].loc[df_out[df_out['ix'] == df_out['ix'].max()].index[0]]

# Compute physical outer bounds of data segments (0.00 to 13.975 inches)
leading_edge_x = first_column_x['x'] - (first_column_x['dx'] / 2.0)
thermal_end_edge_x = last_column_x['x'] + (last_column_x['dx'] / 2.0)

# True targeted CAD envelope parameter limit
blueprint_target_x = 15.75

# A. Leading Edge Slice (Anchored at X = 0.00 in)
leading_slice = df_final[df_final['X'] == first_column_x['x']].copy()
leading_slice['X'] = leading_edge_x
leading_slice['Y'] = leading_edge_x  # Enforce Y = X plane rule at the boundary

# B. Original Element Thermal Grid End Edge Slice (Anchored at X = 13.975 in)
thermal_end_slice = df_final[df_final['X'] == last_column_x['x']].copy()
thermal_end_slice['X'] = thermal_end_edge_x
thermal_end_slice['Y'] = thermal_end_edge_x

# C. CAD Envelope Extension Boundary Slice (Anchored at X = 15.75 in)
cad_extension_slice = df_final[df_final['X'] == last_column_x['x']].copy()
cad_extension_slice['X'] = blueprint_target_x
cad_extension_slice['Y'] = blueprint_target_x  # Maintain Y = X symmetry out to the end edge

# Assemble full expanded data grid chronologically
df_export = pd.concat([leading_slice, df_final, thermal_end_slice, cad_extension_slice], ignore_index=True)

# ==========================================
# 6. WRITE CLEAN FEMAP EXPORT FILE 
# ==========================================
output_filename = f"{target_component}_Femap_TrueInchesGrid_{int(actual_time)}s.csv"
output_path = os.path.join(output_folder, output_filename)

df_export.to_csv(output_path, index=False, float_format="%.8f")
data.close()

print(f"\nSuccess! Pinpointed data snapshot index at [{timestep_idx}] matching {actual_time:.2f}s.")
print(f"Total structured coordinates grid points saved: {len(df_export)} lines.")
print(f"File stored here: {output_path}")

messagebox.showinfo("Success", f"Femap grid generation complete with full 15.75\" extension!\nSaved to: {output_path}")
