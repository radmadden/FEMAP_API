"""tkinter front end. tkinter ships with the standard Windows Python installer, so there
is nothing extra to install. The form fills a Settings object; Run starts the pipeline
on a worker thread so the window stays responsive, and log lines come back through a
queue that the window polls every 100 ms (tkinter widgets must only be touched from
the main thread).

Settings are saved to <output folder>/fem_checks.json by every run, and the last-used
form (including the Nastran path) is remembered in ~/.fem_checks_last.json.
"""
from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .settings import UNIT_SYSTEMS, Settings

LAST = Path.home() / ".fem_checks_last.json"

CHECK_LABELS = [
    ("model_summary", "Model summary (counts, element quality)"),
    ("freefree", "Free-free modes + GROUNDCHECK"),
    ("constrained_modes", "Constrained modes (report only)"),
    ("unit_disp", "Unit enforced displacement / rotation"),
    ("gravity", "Unit gravity X/Y/Z (OLOAD vs SPCFORCE)"),
    ("thermal", "Thermal soak (thermal-distortion models)"),
    ("mass_props", "Mass / CG / inertia vs targets"),
]


def _num(text: str, kind=float):
    text = text.strip()
    return None if text == "" else kind(text)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FEM validation checks")
        self.geometry("980x760")
        self.q: queue.Queue = queue.Queue()
        self.worker = None
        s = self._load_last()
        self.v = {}
        self._build(s)
        self.after(100, self._poll)

    # ------------------------------------------------------------- layout
    def _build(self, s: Settings):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=False, padx=8, pady=6)
        main, targets, adv = ttk.Frame(nb), ttk.Frame(nb), ttk.Frame(nb)
        nb.add(main, text="Run")
        nb.add(targets, text="Mass targets")
        nb.add(adv, text="Advanced")

        r = 0
        r = self._file_row(main, r, "Model deck (.dat/.bdf from FEMAP)", "deck", s.deck,
                           [("Nastran deck", "*.dat *.bdf *.nas"), ("All", "*.*")])
        r = self._file_row(main, r, "Nastran executable", "nastran_exe", s.nastran_exe,
                           [("nastran", "nastran*.exe *.exe"), ("All", "*.*")])
        r = self._dir_row(main, r, "Output folder (blank = <deck>_fem_checks)", "output_dir", s.output_dir)
        ttk.Label(main, text="Unit system").grid(row=r, column=0, sticky="w")
        self.v["units"] = tk.StringVar(value=s.units)
        ttk.Combobox(main, textvariable=self.v["units"], values=list(UNIT_SYSTEMS),
                     state="readonly", width=20).grid(row=r, column=1, sticky="w")
        r += 1
        r = self._entry_row(main, r, "Mount grid (blank = auto, nearest CG)", "mount_grid",
                            s.mount_grid or "")
        r = self._entry_row(main, r, "Gravity / constrained-modes SPC set (blank = kinematic mount)",
                            "gravity_spc_set", s.gravity_spc_set or "")
        box = ttk.LabelFrame(main, text="Checks")
        box.grid(row=r, column=0, columnspan=3, sticky="we", pady=4)
        for i, (k, label) in enumerate(CHECK_LABELS):
            self.v[f"chk_{k}"] = tk.BooleanVar(value=s.checks.get(k, False))
            ttk.Checkbutton(box, text=label, variable=self.v[f"chk_{k}"]).grid(
                row=i // 2, column=i % 2, sticky="w", padx=6)
        r += 1
        self.v["autospc_freefree"] = tk.BooleanVar(value=s.autospc_freefree)
        ttk.Checkbutton(main, text="PARAM,AUTOSPC,YES in the free-free run (NO is stricter but may hit singularities)",
                        variable=self.v["autospc_freefree"]).grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        bar = ttk.Frame(main)
        bar.grid(row=r, column=0, columnspan=3, sticky="w", pady=6)
        self.run_btn = ttk.Button(bar, text="Run checks", command=lambda: self._start(True))
        self.run_btn.pack(side="left")
        self.rep_btn = ttk.Button(bar, text="Re-evaluate existing outputs (no Nastran)",
                                  command=lambda: self._start(False))
        self.rep_btn.pack(side="left", padx=6)
        ttk.Button(bar, text="Save settings...", command=self._save_as).pack(side="left", padx=6)
        ttk.Button(bar, text="Load settings...", command=self._load_from).pack(side="left")
        main.columnconfigure(1, weight=1)

        # Mass targets tab
        t = s.targets
        rr = 0
        ttk.Label(targets, text="Leave any field blank to skip it. Inertia is about the CG, in the FEM basic frame.").grid(
            row=rr, column=0, columnspan=4, sticky="w")
        rr += 1
        rr = self._entry_row(targets, rr, "Mass", "t_mass", "" if t.mass is None else t.mass)
        for i, ax in enumerate("xyz"):
            rr = self._entry_row(targets, rr, f"CG {ax}", f"t_cg{i}", "" if t.cg[i] is None else t.cg[i])
        for i, nm in enumerate(["Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz"]):
            rr = self._entry_row(targets, rr, nm, f"t_I{i}", "" if t.inertia[i] is None else t.inertia[i])
        rr = self._entry_row(targets, rr, "Mass tolerance (%)", "t_tol_mass", t.tol_mass_pct)
        rr = self._entry_row(targets, rr, "CG tolerance (length)", "t_tol_cg", t.tol_cg_length)
        rr = self._entry_row(targets, rr, "Inertia tolerance (%)", "t_tol_I", t.tol_inertia_pct)
        ttk.Label(targets, text="Target mass units").grid(row=rr, column=0, sticky="w")
        self.v["t_mass_unit"] = tk.StringVar(value=t.mass_unit)
        ttk.Combobox(targets, textvariable=self.v["t_mass_unit"], values=["model", "lbm", "kg"],
                     state="readonly", width=10).grid(row=rr, column=1, sticky="w")
        rr += 1
        ttk.Label(targets, text="Products of inertia from CAD are").grid(row=rr, column=0, sticky="w")
        self.v["t_conv"] = tk.StringVar(value=t.product_convention)
        ttk.Combobox(targets, textvariable=self.v["t_conv"], values=["cad", "tensor"],
                     state="readonly", width=10).grid(row=rr, column=1, sticky="w")
        ttk.Label(targets, text="cad: Ixy = +∫xy dm (SolidWorks/NX/Creo).  tensor: Ixy = −∫xy dm.").grid(
            row=rr, column=2, sticky="w")

        # Advanced tab
        th = s.thresholds
        ra = 0
        for key, label, val in [
            ("n_modes_freefree", "Free-free modes (>= 7)", s.n_modes_freefree),
            ("n_modes_constrained", "Constrained modes", s.n_modes_constrained),
            ("alpha", "Thermal CTE used for the soak", s.alpha),
            ("th_rbm_ratio", "Rigid/elastic frequency ratio limit", th.rbm_ratio),
            ("th_gravity_rel", "Gravity OLOAD/SPCFORCE/weight limit", th.gravity_rel),
            ("th_ud_disp_rel", "Unit-disp field error limit", th.ud_disp_rel),
            ("th_thermal_disp_rel", "Thermal displacement error limit", th.thermal_disp_rel),
            ("th_thermal_stress_rel", "Thermal stress / (E alpha dT) limit", th.thermal_stress_rel),
            ("th_force_tol", "Force limit (blank = from units)", "" if th.force_tol is None else th.force_tol),
            ("th_moment_tol", "Moment limit (blank = from units)", "" if th.moment_tol is None else th.moment_tol),
            ("run_timeout_s", "Nastran timeout per run, s (blank = none)", "" if s.run_timeout_s is None else s.run_timeout_s),
            ("nastran_args", "Nastran arguments", " ".join(s.nastran_args)),
        ]:
            ra = self._entry_row(adv, ra, label, key, val)

        self.log = tk.Text(self, height=18, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _entry_row(self, parent, r, label, key, value):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=1)
        self.v[key] = tk.StringVar(value=str(value))
        ttk.Entry(parent, textvariable=self.v[key], width=24).grid(row=r, column=1, sticky="w")
        return r + 1

    def _file_row(self, parent, r, label, key, value, types):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=1)
        self.v[key] = tk.StringVar(value=value)
        ttk.Entry(parent, textvariable=self.v[key], width=70).grid(row=r, column=1, sticky="we")
        ttk.Button(parent, text="Browse...", command=lambda: self.v[key].set(
            filedialog.askopenfilename(filetypes=types) or self.v[key].get())).grid(row=r, column=2)
        return r + 1

    def _dir_row(self, parent, r, label, key, value):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=1)
        self.v[key] = tk.StringVar(value=value)
        ttk.Entry(parent, textvariable=self.v[key], width=70).grid(row=r, column=1, sticky="we")
        ttk.Button(parent, text="Browse...", command=lambda: self.v[key].set(
            filedialog.askdirectory() or self.v[key].get())).grid(row=r, column=2)
        return r + 1

    # ------------------------------------------------------------- settings
    def _collect(self) -> Settings:
        g = lambda k: self.v[k].get()  # noqa: E731
        s = Settings()
        s.deck, s.nastran_exe, s.output_dir = g("deck").strip(), g("nastran_exe").strip(), g("output_dir").strip()
        s.units = g("units")
        s.mount_grid = _num(g("mount_grid"), int) or 0
        s.gravity_spc_set = _num(g("gravity_spc_set"), int) or 0
        s.checks = {k: bool(self.v[f"chk_{k}"].get()) for k, _ in CHECK_LABELS}
        s.autospc_freefree = bool(self.v["autospc_freefree"].get())
        s.n_modes_freefree = int(g("n_modes_freefree"))
        s.n_modes_constrained = int(g("n_modes_constrained"))
        s.alpha = float(g("alpha"))
        s.run_timeout_s = _num(g("run_timeout_s"))
        s.nastran_args = g("nastran_args").split()
        th = s.thresholds
        th.rbm_ratio = float(g("th_rbm_ratio"))
        th.gravity_rel = float(g("th_gravity_rel"))
        th.ud_disp_rel = float(g("th_ud_disp_rel"))
        th.thermal_disp_rel = float(g("th_thermal_disp_rel"))
        th.thermal_stress_rel = float(g("th_thermal_stress_rel"))
        th.force_tol = _num(g("th_force_tol"))
        th.moment_tol = _num(g("th_moment_tol"))
        t = s.targets
        t.mass = _num(g("t_mass"))
        t.cg = [_num(g(f"t_cg{i}")) for i in range(3)]
        t.inertia = [_num(g(f"t_I{i}")) for i in range(6)]
        t.tol_mass_pct = float(g("t_tol_mass"))
        t.tol_cg_length = float(g("t_tol_cg"))
        t.tol_inertia_pct = float(g("t_tol_I"))
        t.mass_unit = g("t_mass_unit")
        t.product_convention = g("t_conv")
        return s

    def _load_last(self) -> Settings:
        try:
            return Settings.from_dict(json.loads(LAST.read_text()))
        except Exception:
            return Settings()

    def _save_as(self):
        try:
            s = self._collect()
        except ValueError as exc:
            messagebox.showerror("Settings", f"Check the form: {exc}")
            return
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if p:
            s.to_json(Path(p))

    def _load_from(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not p:
            return
        for w in self.winfo_children():
            w.destroy()
        self.v = {}
        self._build(Settings.from_json(Path(p)))

    # ------------------------------------------------------------- running
    def _start(self, run_solver: bool):
        if self.worker and self.worker.is_alive():
            return
        try:
            s = self._collect()
        except ValueError as exc:
            messagebox.showerror("Settings", f"Check the form: {exc}")
            return
        problems = s.validate(need_nastran=run_solver)
        if problems:
            messagebox.showerror("Settings", "\n".join(problems))
            return
        try:
            LAST.write_text(json.dumps(asdict(s), indent=2))
        except OSError:
            pass
        self.run_btn.state(["disabled"])
        self.rep_btn.state(["disabled"])

        def work():
            from .pipeline import run
            summary = run(s, lambda m: self.q.put(("log", m)), run_solver=run_solver)
            self.q.put(("done", summary))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", payload + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                else:
                    self.run_btn.state(["!disabled"])
                    self.rep_btn.state(["!disabled"])
                    if payload.error:
                        messagebox.showerror("FEM checks", payload.error)
                    elif payload.report:
                        if messagebox.askyesno("FEM checks", f"Overall: {payload.overall}\n\nOpen the report?"):
                            import os
                            try:
                                os.startfile(str(payload.report))  # Windows only
                            except AttributeError:
                                messagebox.showinfo("FEM checks", str(payload.report))
        except queue.Empty:
            pass
        self.after(100, self._poll)


def launch():
    App().mainloop()
