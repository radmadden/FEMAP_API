"""Builds a small FEMAP-style deck for tests: plate + solid + bar + RBE2 + RBE3 + CBUSH + CONM2.

It is not physically meaningful; it exercises every code path the real deck will hit:
shells and bars (rotational grids), a CHEXA (solid-only grids), an RBE2 spider with an
independent grid near the CG, an RBE3 (dependent reference grid), a coincident-grid
CBUSH, a CONM2, an existing SPC set, NSM on the case-control side, and a FEMAP checksum
on the ENDDATA line.
"""
from pathlib import Path

DECK = """\
ID BD,FEMAP
SOL SESTATIC
CEND
  TITLE = TEST MODEL
  ECHO = NONE
  NSM = 50
  DISPLACEMENT(PLOT) = ALL
  SPC = 1
SUBCASE 1
  LOAD = 1
BEGIN BULK
PARAM,POST,-1
PARAM,AUTOSPC,YES
PARAM,K6ROT,100.
PARAM,GRDPNT,0
$ plate 1..25 in the z=0 plane, 4x4 CQUAD4
{grids}
{quads}
PSHELL,1,1,0.1,1,,1
MAT1,1,3.0e7,,0.3,7.3e-4,6.5e-6,70.
$ solid block (CHEXA) beside the plate, MAT1 2
GRID,101,,5.,0.,0.
GRID,102,,6.,0.,0.
GRID,103,,6.,1.,0.
GRID,104,,5.,1.,0.
GRID,105,,5.,0.,1.
GRID,106,,6.,0.,1.
GRID,107,,6.,1.,1.
GRID,108,,5.,1.,1.
CHEXA,200,2,101,102,103,104,105,106,+
+,107,108
PSOLID,2,2
MAT1,2,1.0e7,,0.33,2.5e-4,1.3e-5,20.
$ bar from plate corner 5 to solid grid 101
CBAR,300,3,5,101,0.,0.,1.
PBAR,3,1,0.1,1.e-3,1.e-3,2.e-3
$ RBE2 spider: independent 201 above the plate centre, four plate grids dependent
GRID,201,,2.,2.,0.5
RBE2,400,201,123456,7,9,17,19
CONM2,500,201,,0.01
$ CBUSH between coincident grids 201 and 202, RBE3 reference grid 203
GRID,202,,2.,2.,0.5
CBUSH,600,6,201,202,,,,0
PBUSH,6,K,1.e6,1.e6,1.e6,1.e6,1.e6,1.e6
GRID,203,,2.,2.,1.0
RBE3,700,,203,123456,1.0,123,202,13
$ model's own SPC set 1 and a load set 1
SPC1,1,123456,1,21
FORCE,1,25,0,1.0,0.,0.,-1.
NSM1,50,PSHELL,1.e-5,ALL
ENDDATA 9f3a17c2
"""


def _plate():
    grids, quads = [], []
    for j in range(5):
        for i in range(5):
            nid = j * 5 + i + 1
            grids.append(f"GRID,{nid},,{float(i)},{float(j)},0.")
    eid = 1
    for j in range(4):
        for i in range(4):
            n1 = j * 5 + i + 1
            quads.append(f"CQUAD4,{eid},1,{n1},{n1 + 1},{n1 + 6},{n1 + 5}")
            eid += 1
    return "\n".join(grids), "\n".join(quads)


def write_test_deck(path: Path) -> Path:
    g, q = _plate()
    Path(path).write_text(DECK.format(grids=g, quads=q))
    return Path(path)


if __name__ == "__main__":
    import sys
    print(write_test_deck(Path(sys.argv[1] if len(sys.argv) > 1 else "test_model.dat")))
