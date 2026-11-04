"""Parser tests against the reference Simcenter .f06 files in fem_checks/reference_model.
Expected numbers are copied from those files by hand."""
from pathlib import Path

import pytest

from fem_checks.f06 import parse_f06

REF = Path(__file__).resolve().parents[1] / "reference_model"
FREE = REF / "freefree_check-0000.f06"
UD = REF / "ud_check-0000.f06"
GRAV = REF / "1g_unit_load_check-0000.f06"

need = pytest.mark.skipif(not (FREE.exists() and UD.exists() and GRAV.exists()),
                          reason="sample .f06 files not in repo")


@need
def test_freefree():
    f = parse_f06(FREE)
    assert f.completed and f.version == "2512.0095"
    assert [s.set_name for s in f.groundcheck] == ["G", "N", "N+AUTOSPC", "F", "A"]
    g = f.groundcheck[0]
    assert g.limit == pytest.approx(2.337077) and g.all_pass
    assert g.strain_energy[3] == pytest.approx(1.209146e-04)
    assert f.groundcheck[4].limit == pytest.approx(1.177637e1)
    assert len(f.modes) == 20
    assert f.modes[0].cycles == pytest.approx(4.286750e-04)
    assert f.modes[6].cycles == pytest.approx(1.330038e02)
    assert f.modes[0].eigenvalue == pytest.approx(-7.254643e-06)
    gset = [b for b in f.mass_blocks if b.dof_set == "G"][0]
    assert gset.mass == pytest.approx([1.900295] * 3)
    assert gset.IS[0] == pytest.approx([4.602007e02, 5.385219e00, 2.476800e-05])
    assert gset.cg[1][0] == pytest.approx(3.960659e-01)
    assert gset.cg[0][1] == pytest.approx(-4.432694)
    assert len(gset.MO) == 6 and gset.MO[3][4] == pytest.approx(-2.048988)
    reduced = [b for b in f.mass_blocks if b.dof_set and b.dof_set != "G"]
    assert reduced and all(b.change_pct == [0.0] * 6 for b in reduced)
    assert len(f.meff_fractions) == 20
    assert f.meff_fractions[0].fraction["T1"] == pytest.approx(4.226125e-02)
    assert f.meff_fractions[0].fraction["R3"] == pytest.approx(9.552853e-02)
    assert f.meff_fractions[19].cumsum["R2"] == pytest.approx(1.0)
    assert f.meff_totals["T1"] == pytest.approx(1.0)
    assert f.singularities == [(51879, 51879)]
    assert [m.number for m in f.messages] == ["2125"] and not f.fatals


@need
def test_unit_disp():
    f = parse_f06(UD)
    assert sorted(f.spcforce) == [1, 2, 3, 4, 5, 6]
    assert f.spcforce[1][0] == pytest.approx(-5.013378e-06)
    assert f.spcforce[3][3] == pytest.approx(1.374222e-04)
    assert f.oload[6] == [0.0] * 6
    assert len(f.epsilon) == 6 and f.epsilon[2]["epsilon"] == pytest.approx(-3.6554742e-15)
    gp = [b for b in f.mass_blocks if b.source == "GPWG"]
    assert gp and gp[0].mass[0] == pytest.approx(1.900295)


@need
def test_gravity():
    f = parse_f06(GRAV)
    assert f.version == "2306.0084"  # the older Simcenter install
    assert f.oload[1][0] == pytest.approx(7.337038e02)
    assert f.oload[1][5] == pytest.approx(3.252285e03)
    assert f.spcforce[1][0] == pytest.approx(-7.337038e02)
    assert f.spcforce[3][3] == pytest.approx(3.252285e03)
    assert [e["seq"] for e in f.epsilon] == [1, 2, 3]
