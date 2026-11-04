"""Model handling, deck writing and check logic on the synthetic test deck, plus the
check evaluations that can run on the reference .f06 files."""
from pathlib import Path

import numpy as np
import pytest

from fem_checks import checks as C
from fem_checks import decks as D
from fem_checks import model as M
from fem_checks import results as R
from fem_checks.f06 import parse_f06
from fem_checks.settings import UNIT_SYSTEMS, MassTargets, Settings, Thresholds
from fem_checks.tests.make_test_model import write_test_deck

REF = Path(__file__).resolve().parents[1] / "reference_model"   # reference model + outputs


@pytest.fixture()
def deck(tmp_path):
    return write_test_deck(tmp_path / "model.dat")


@pytest.fixture()
def model(deck, tmp_path):
    return M.read_model(deck, tmp_path / "work")


def test_checksum_is_stripped_and_model_reads(deck, model):
    assert len(model.nodes) == 36
    assert "NSM = 50" in M.carried_case_control(deck)


def test_mount_autopick_and_rejections(deck, model):
    info = M.model_info(model, deck)
    mount = M.choose_mount(model, info)
    assert mount.grid == 201  # RBE2 independent grid nearest the CG
    with pytest.raises(ValueError, match="dependent"):
        M.choose_mount(model, info, 7)      # RBE2 dependent grid
    assert M.choose_mount(model, info, 1).grid == 1   # in model SPC set 1: allowed
    model.nodes[13].ps = "456"
    with pytest.raises(ValueError, match="permanent constraint"):
        M.choose_mount(model, info, 13)     # GRID PS field is always active
    with pytest.raises(ValueError, match="rotational stiffness"):
        M.choose_mount(model, info, 107)    # CHEXA-only grid


def test_bulk_and_thermal(deck, model, tmp_path):
    text = M.bulk_text(model)
    assert "AUTOSPC" not in text and "GRDPNT" not in text and "K6ROT" in text
    assert "ENDDATA" not in text and "BEGIN BULK" not in text
    m2 = M.read_model(deck, tmp_path / "work")
    log = M.make_thermal_uniform(m2, 2.0e-5)
    assert all(m.a == 2.0e-5 and m.tref == 0.0 for m in m2.materials.values())
    assert all(r.alpha == 2.0e-5 for r in m2.rigid_elements.values())
    assert "MAT1" in log[0]


def test_reserved_ids_avoid_collisions(model):
    assert M.reserved_base(model) == 9_000_000
    from pyNastran.bdf.cards.constraints import SPC1
    model.add_spc1(9_000_001, "123", [1])
    assert M.reserved_base(model) == 10_000_000


def test_decks(tmp_path):
    ids = D.DeckIds(9_000_000)
    trace = {"title": "1G UNIT LOAD CHECK", "subtitle": "BD IRAD V2", "deck": "x.dat",
             "femap_model": "D:\\IRAD\\BD_IRAD_FEMv4.modfem",
             "nastran_statements": ["NASTRAN SYSTEM(442)=-1,SYSTEM(319)=1"]}
    paths = D.write_decks(tmp_path, list(D.DECK_NAMES), ids, 201, ids.mount_spc, 2, 386.0886,
                          10, 10, True, ["NSM = 50"], trace, D.safe_stem("D:/x/gimbal assy full.dat"))
    assert paths["freefree"].name == "01_gimbal_assy_full_freefree.dat"
    assert paths["thermal"].name == "05_gimbal_assy_full_thermal.dat"
    ff = paths["freefree"].read_text()
    assert "GROUNDCHECK(PRINT,SET=(G,N,N+AUTOSPC,F,A),GRID=201,DATAREC=YES)=YES" in ff
    assert "SPC =" not in ff and "NSM = 50" in ff
    assert "TITLE = 1G UNIT LOAD CHECK" in ff and "SUBTITLE = BD IRAD V2" in ff
    assert "BD_IRAD_FEMv4.modfem" in ff
    assert ff.index("NASTRAN SYSTEM(442)") < ff.index("SOL 103")
    assert "SPC = 2" in paths["constrained_modes"].read_text()
    assert "SPC1" not in paths["constrained_modes"].read_text()
    ud = paths["unit_disp"].read_text()
    assert "SPC1,9000001,123456,201" in ud and "SPCD,9000016,201,6,1.0" in ud
    th = paths["thermal"].read_text()
    assert "RIGID = LAGRAN" in th and "TEMPERATURE(INITIAL) = 9000031" in th
    assert "INCLUDE 'gimbal_assy_full_bulk_thermal.bdf'" in th
    assert "INCLUDE 'gimbal_assy_full_bulk.bdf'" in ff
    gr = paths["gravity"].read_text()
    assert "GRAV,9000021,0,386.089,1.0,0.0,0.0" in gr
    for p in paths.values():
        assert max(len(x) for x in p.read_text().splitlines()) <= 80


def test_rigid_body_field_rotation():
    xyz = np.array([[1.0, 0, 0], [0, 2.0, 0]])
    t, r = R.rigid_body_field(xyz, np.zeros(3), 6)  # unit rotation about Z
    assert t[0] == pytest.approx([0, 1, 0]) and t[1] == pytest.approx([-2, 0, 0])
    assert r[0] == pytest.approx([0, 0, 1])


def test_field_error_detects_grounding():
    xyz = np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0]])
    t_exp, r_exp = R.rigid_body_field(xyz, np.zeros(3), 1)
    good = R.Field(np.array([1, 2, 3]), xyz, t_exp.copy(), r_exp.copy())
    assert R.field_error(good, t_exp, r_exp)["t_err"] == 0.0
    bad_t = t_exp.copy()
    bad_t[2] = [0.5, 0, 0]  # grid 3 held back by something
    bad = R.Field(np.array([1, 2, 3]), xyz, bad_t, r_exp.copy())
    err = R.field_error(bad, t_exp, r_exp)
    assert err["t_err"] == pytest.approx(0.5) and err["worst_t"][0][0] == 3


@pytest.mark.skipif(not (REF / "freefree_check-0000.f06").exists(), reason="samples missing")
def test_freefree_check_on_sample():
    res = C.check_freefree(parse_f06(REF / "freefree_check-0000.f06"), Thresholds(), True)
    assert res.status == "PASS"
    assert res.data["ratio"] == pytest.approx(4.286750e-04 / 1.330038e02)
    assert "n_over_abs" not in res.data   # Book absolute limit not used (design decision D1)
    strict = Thresholds(rbm_ratio=1e-7)
    assert C.check_freefree(parse_f06(REF / "freefree_check-0000.f06"), strict, True).status == "FAIL"


@pytest.mark.skipif(not (REF / "1g_unit_load_check-0000.f06").exists(), reason="samples missing")
def test_gravity_check_on_sample():
    f = parse_f06(REF / "1g_unit_load_check-0000.f06")
    u = UNIT_SYSTEMS["in-lbf-s"]
    # The sample was run with g = 386.1; the check must agree with OLOAD to 1e-4.
    res = C.check_gravity(f, 1.900295, 386.1, 30.0, Thresholds(), u, "test")
    assert res.status == "PASS"
    res_bad = C.check_gravity(f, 1.900295 * 1.01, 386.1, 30.0, Thresholds(), u, "test")
    assert res_bad.status == "FAIL"  # 1% mass error is caught by the weight comparison


@pytest.mark.skipif(not (REF / "freefree_check-0000.f06").exists(), reason="samples missing")
def test_mass_props_on_sample():
    f = parse_f06(REF / "freefree_check-0000.f06")
    block = [b for b in f.mass_blocks if b.dof_set == "G"][0]
    u = UNIT_SYSTEMS["in-lbf-s"]
    t = MassTargets(mass=1.9, cg=[0.396, -4.433, None], inertia=[460.2, None, None, 5.385, None, None])
    assert C.check_mass_props(block, t, "in-lbf-s", u).status == "PASS"
    # Same numbers typed as tensor products -> sign flips -> Ixy off by 2*5.385 = 2.3% of max I.
    t.product_convention = "tensor"
    t.tol_inertia_pct = 1.0
    assert C.check_mass_props(block, t, "in-lbf-s", u).status == "FAIL"
    t2 = MassTargets(mass=1.900295 * 386.0886, mass_unit="lbm")   # lbm converts to lbf-s^2/in
    assert C.check_mass_props(block, t2, "in-lbf-s", u).status == "PASS"
    assert C.check_mass_props(block, MassTargets(), "in-lbf-s", u).status == "SKIPPED"


def test_settings_roundtrip(tmp_path):
    s = Settings(deck="x.dat", units="mm-t-s")
    s.targets.mass = 2.0
    s.to_json(tmp_path / "s.json")
    s2 = Settings.from_json(tmp_path / "s.json")
    assert s2.units == "mm-t-s" and s2.targets.mass == 2.0
    assert s2.force_tol() == pytest.approx(0.44482, rel=1e-4)
    assert "Model deck not found" in " ".join(s2.validate(need_nastran=False))


REAL_DECK = REF / "1G_unit_load_check-0000.dat"


@pytest.mark.skipif(not REAL_DECK.exists(), reason="reference model deck not in repo")
def test_real_deck_trace_and_roundtrip(tmp_path):
    """The reference model: traceability fields, mount checks, and a lossless bulk rewrite."""
    tr = M.read_trace(REAL_DECK)
    assert tr["title"] == "1G UNIT LOAD CHECK" and tr["subtitle"] == "BD IRAD V2"
    assert tr["femap_model"].endswith("BD_IRAD_FEMv4.modfem")
    assert tr["nastran_statements"] == ["INIT MASTER(S)", "NASTRAN SYSTEM(442)=-1,SYSTEM(319)=1"]
    model = M.read_model(REAL_DECK, tmp_path)
    info = M.model_info(model, REAL_DECK, quality=False)
    assert info.n_grids == 24851
    assert M.choose_mount(model, info, 199999).grid == 199999   # in model SPC sets 2/3: allowed
    (tmp_path / "bulk.bdf").write_text(M.bulk_text(model))
    (tmp_path / "rt.dat").write_text(f"SOL 101\nCEND\nBEGIN BULK\nINCLUDE '{tmp_path / 'bulk.bdf'}'\nENDDATA\n")
    from pyNastran.bdf.bdf import BDF
    back = BDF(debug=False, mode="nx")
    back.read_bdf(str(tmp_path / "rt.dat"), xref=False)
    for name in ("nodes", "elements", "properties", "materials", "rigid_elements", "masses", "coords"):
        a, b = getattr(model, name), getattr(back, name)
        assert set(a) == set(b), name
        for k in a:
            assert a[k].repr_fields() == b[k].repr_fields(), (name, k)


# ------------------------------------------------------------- setup_env
def test_setup_python_version_gate():
    from fem_checks.setup_env import python_ok
    assert python_ok((3, 12, 0))[0] and python_ok((3, 10, 1))[0]
    ok, msg = python_ok((3, 13, 0))
    assert not ok and "3.12" in msg


def test_setup_vscode_merge_keeps_existing_values(tmp_path):
    import json
    from fem_checks.setup_env import write_vscode
    vs = tmp_path / ".vscode"
    vs.mkdir()
    (vs / "settings.json").write_text(json.dumps({"python.defaultInterpreterPath": "C:/mine/python.exe",
                                                  "editor.fontSize": 15}))
    (vs / "launch.json").write_text(json.dumps({"version": "0.2.0", "configurations": [
        {"name": "FEM checks: GUI", "type": "debugpy", "request": "launch", "module": "custom"}]}))
    write_vscode(tmp_path)
    write_vscode(tmp_path)   # idempotent
    st = json.loads((vs / "settings.json").read_text())
    assert st["python.defaultInterpreterPath"] == "C:/mine/python.exe" and st["editor.fontSize"] == 15
    assert st["python.testing.pytestArgs"] == ["fem_checks/tests"]
    la = json.loads((vs / "launch.json").read_text())
    names = [c["name"] for c in la["configurations"]]
    assert names.count("FEM checks: GUI") == 1 and la["configurations"][0]["module"] == "custom"
    assert len(la["inputs"]) == 1
    ex = json.loads((vs / "extensions.json").read_text())
    assert ex["recommendations"] == ["ms-python.python", "ms-toolsai.jupyter"]


def test_setup_find_nastran(tmp_path):
    """Regression: nastranw.exe (a launcher) must never be picked over nastran.exe."""
    from fem_checks.setup_env import find_nastran
    from fem_checks.runner import solver_exe
    for ver in ("2512", "2606"):
        d = tmp_path / f"Femap {ver}" / "NASTRAN" / "bin"
        d.mkdir(parents=True)
        (d / "nastran.exe").write_text("")
        (d / "nastranw.exe").write_text("")
    found = find_nastran([str(tmp_path / "Femap*" / "NASTRAN" / "bin" / "nastran*.exe")])
    assert "2606" in found[0] and all(Path(f).name == "nastran.exe" for f in found)
    w = tmp_path / "Femap 2606" / "NASTRAN" / "bin" / "nastranw.exe"
    assert Path(solver_exe(str(w), lambda m: None)).name == "nastran.exe"


def test_long_case_control_lines_do_not_stop_bulk_write(tmp_path):
    """Regression: 'AssertionError: len(msg)=77' from pyNastran re-writing a long
    FEMAP case-control line. bulk_text must not write case control at all."""
    from fem_checks.tests.make_test_model import DECK, _plate
    g, q = _plate()
    long_cc = ("  MEFFMASS(PLOT,PRINT,GRID=0,PARTFAC,MEFFM,MEFFW,FRACSUM,SUMMARY) = YES\n"
               "  TITLE = A VERY LONG TITLE THAT GOES ON AND ON FOR THE TRACEABILITY OF IT ALL\n")
    d = tmp_path / "long.dat"
    d.write_text(DECK.format(grids=g, quads=q).replace("  ECHO = NONE\n", "  ECHO = NONE\n" + long_cc))
    m = M.read_model(d, tmp_path / "work")
    before = m.punch
    text = M.bulk_text(m)
    assert "GRID" in text and "MEFFMASS" not in text
    assert "SOL " not in text and "CEND" not in text   # no executive control leaked in
    assert m.punch == before                           # pyNastran flag restored


def test_method_appendix_states_run_values():
    """The Method appendix must quote the thresholds and mount actually used in the run."""
    from fem_checks.method import method_sections
    s = Settings(units="mm-t-s", real_spc_set=7)
    s.checks["thermal"] = True
    s.thresholds.rbm_ratio = 3e-5
    secs = dict(method_sections(s, UNIT_SYSTEMS["mm-t-s"], 4242, False, "kinematic mount",
                                "model SPC set 7", 2e-5))
    assert {"General", "Free-free modes and GROUNDCHECK", "Unit gravity", "Thermal soak",
            "References"} <= set(secs)
    text = " ".join(" ".join(v) for v in secs.values())
    assert "3e-05" in text and "4242" in text and "9806.65" in text and "N-mm" in text
    assert "alpha = 2e-05" in text and "model SPC set 7" in text
    assert "Mass properties" not in secs        # no targets entered -> not described


def test_cd_axes_match_pynastran_geometry():
    """Output-system axes for R/C/S systems must equal the direction a grid moves when one
    local coordinate changes (finite difference through pyNastran's own transform)."""
    from pyNastran.bdf.bdf import BDF
    m = BDF(debug=False)
    m.add_cord2r(2, [1., 2., 3.], [1., 2., 4.], [2., 3., 3.])
    m.add_cord2c(3, [0.5, -1., 2.], [0.5, -1., 3.], [1.5, -1., 2.])
    m.add_cord2s(4, [1., 2., 3.], [1., 2., 4.], [2., 2., 3.])
    m.cross_reference()
    rng = np.random.default_rng(1)
    for cid in (2, 3, 4):
        c = m.coords[cid]
        loc = rng.uniform([1.0, 20.0, 10.0], [3.0, 70.0, 80.0], size=(5, 3))  # r/x, theta/y (deg), z/phi
        xyz = np.array([c.transform_node_to_global(p) for p in loc])
        ax = R.cd_axes(c, xyz)
        for k in range(5):
            for a in range(3):
                d = np.zeros(3); d[a] = 1e-6   # tiny step in one local coordinate
                step = c.transform_node_to_global(loc[k] + d) - xyz[k]
                step /= np.linalg.norm(step)
                assert ax[k, a] == pytest.approx(step, abs=1e-5), (c.Type, a)


def test_displacements_rotated_by_grid_id_not_position():
    """A local radial displacement at a cylindrical-CD grid becomes the basic radial
    direction, matched by grid ID even when the OP2 holds extra non-GRID rows."""
    from pyNastran.bdf.bdf import BDF
    m = BDF(debug=False)
    m.add_cord2c(3, [0., 0., 0.], [0., 0., 1.], [1., 0., 0.])
    m.add_grid(10, [0., 2., 0.], cd=3)    # on +y: e_r = +y, e_theta = -x
    m.add_grid(20, [5., 0., 0.])          # basic output
    m.cross_reference()

    class Disp:   # minimal stand-in for a pyNastran displacement table
        node_gridtype = np.array([[7, 2], [20, 1], [10, 1]])   # row 0: a scalar point
        data = np.array([[[9, 9, 9, 9, 9, 9], [0, 0, 1, 0, 0, 0], [1, 0, 0, 0, 1, 0]]], float)

    class Op2:
        displacements = {1: Disp()}

    f = R.displacements_basic(Op2(), m)[1]
    assert list(f.nids) == [20, 10]
    assert f.t[1] == pytest.approx([0, 1, 0]) and f.r[1] == pytest.approx([-1, 0, 0])
    assert f.t[0] == pytest.approx([0, 0, 1])


def test_mount_with_cylindrical_cd(deck, tmp_path):
    """A mount grid with a cylindrical output system is accepted; the enforced DOF act
    along its local axes and the rigid-body field follows them."""
    model = M.read_model(deck, tmp_path / "work")
    model.add_cord2c(9, [2., 0., 0.5], [2., 0., 1.5], [3., 0., 0.5])
    model.cross_reference()
    model.nodes[201].cd = 9        # RBE2 independent grid at (2, 2, 0.5): on local +y
    info = M.model_info(model, deck, quality=False)
    m = M.choose_mount(model, info, 201)
    assert m.cd == 9
    assert m.axes[0] == pytest.approx([0, 1, 0]) and m.axes[1] == pytest.approx([-1, 0, 0])
    xyz = np.array([[2., 2., 0.5], [2., 3., 0.5]])
    t, r = R.rigid_body_field(xyz, np.array(m.xyz), 1, m.axes)   # unit local "r" = basic +y
    assert t[1] == pytest.approx([0, 1, 0])
    t, r = R.rigid_body_field(xyz, np.array(m.xyz), 6, m.axes)   # rotation about local z
    assert r[0] == pytest.approx([0, 0, 1]) and t[1] == pytest.approx([-1, 0, 0])
