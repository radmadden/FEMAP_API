"""Model handling, deck writing and check logic on the synthetic test deck, plus the
check evaluations that can run on the user's real .f06 files."""
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

REPO = Path(__file__).resolve().parents[2]


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
                          10, 10, True, ["NSM = 50"], trace)
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
    assert "INCLUDE 'model_bulk_thermal.bdf'" in th
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


@pytest.mark.skipif(not (REPO / "freefree_check-0000.f06").exists(), reason="samples missing")
def test_freefree_check_on_sample():
    res = C.check_freefree(parse_f06(REPO / "freefree_check-0000.f06"), Thresholds(), True)
    assert res.status == "PASS"
    assert res.data["ratio"] == pytest.approx(4.286750e-04 / 1.330038e02)
    assert "n_over_abs" not in res.data   # Book absolute limit dropped (plan decision #1)
    strict = Thresholds(rbm_ratio=1e-7)
    assert C.check_freefree(parse_f06(REPO / "freefree_check-0000.f06"), strict, True).status == "FAIL"


@pytest.mark.skipif(not (REPO / "1g_unit_load_check-0000.f06").exists(), reason="samples missing")
def test_gravity_check_on_sample():
    f = parse_f06(REPO / "1g_unit_load_check-0000.f06")
    u = UNIT_SYSTEMS["in-lbf-s"]
    # The sample was run with g = 386.1; the check must agree with OLOAD to 1e-4.
    res = C.check_gravity(f, 1.900295, 386.1, 30.0, Thresholds(), u, "test")
    assert res.status == "PASS"
    res_bad = C.check_gravity(f, 1.900295 * 1.01, 386.1, 30.0, Thresholds(), u, "test")
    assert res_bad.status == "FAIL"  # 1% mass error is caught by the weight comparison


@pytest.mark.skipif(not (REPO / "freefree_check-0000.f06").exists(), reason="samples missing")
def test_mass_props_on_sample():
    f = parse_f06(REPO / "freefree_check-0000.f06")
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


REAL_DECK = REPO / "1G_unit_load_check-0000.dat"


@pytest.mark.skipif(not REAL_DECK.exists(), reason="user's full model deck not in repo")
def test_real_deck_trace_and_roundtrip(tmp_path):
    """The user's full model: traceability fields, mount checks, and a lossless bulk rewrite."""
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


def test_setup_vscode_merge_keeps_user_values(tmp_path):
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
    from fem_checks.setup_env import find_nastran
    for ver in ("2412", "2512"):
        d = tmp_path / f"FEMAP {ver}" / "NASTRAN" / "bin"
        d.mkdir(parents=True)
        (d / "nastran.exe").write_text("")
    found = find_nastran([str(tmp_path / "FEMAP*" / "NASTRAN" / "bin" / "nastran*.exe")])
    assert "2512" in found[0]   # newest first
