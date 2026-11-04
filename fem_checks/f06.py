"""Parsers for the Simcenter Nastran .f06 tables that pyNastran's OP2 reader doesn't give us.

Every parser works on the list of text lines and returns plain Python data.
They were written against real Simcenter output (the sample files
freefree_check-0000.f06, ud_check-0000.f06 and 1g_unit_load_check-0000.f06 in the
repo, versions 2512 and 4/24/23). They key on table headings, never on the version
banner, and they tolerate page breaks: a page break in an .f06 is a line starting with
"1" holding the title and "PAGE n", followed by an optional subtitle/label line and a
"0" carriage-control line.

Tables parsed (with the .f06 heading they key on):
  GROUNDCHECK          "RESULTS OF RIGID BODY CHECKS OF MATRIX ... (X-SET)"   qrg ~18934
  WEIGHTCHECK / GPWG   "O U T P U T   F R O M   W E I G H T   C H E C K"      qrg ~31138
                       "O U T P U T   F R O M   G R I D   P O I N T   W E I G H T ..."
  eigenvalues          "R E A L   E I G E N V A L U E S"
  effective mass       "MODAL EFFECTIVE MASS FRACTION" (MEFFMASS FRACSUM)      qrg ~22311
  resultants           "OLOAD    RESULTANT" / "SPCFORCE RESULTANT"
  EPSILON              "LOAD SEQ. NO.   EPSILON" (UIM 5293)
  singularities        "*** n SINGULARITIES FOUND  m SINGULARITIES ELIMINATED"
  messages             "*** USER|SYSTEM FATAL|WARNING MESSAGE nnnn (MODULE)"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# A Nastran E-format real, e.g. -7.254643E-06. Plain decimals are accepted too.
_F = r"[-+]?\d+\.\d*(?:[EeDd][-+]?\d+)?"
_FLOAT_RE = re.compile(_F)
_NUM = r"\s+(" + _F + r")"   # whitespace then one captured real


def _floats(line: str) -> list[float]:
    return [float(x.replace("D", "E").replace("d", "e")) for x in _FLOAT_RE.findall(line)]


def _is_page_header(line: str) -> bool:
    """'1    FREE FREE MODES   ...   PAGE    25' style lines."""
    return line.startswith("1") and "PAGE" in line


def read_lines(path) -> list[str]:
    # .f06 files are plain ASCII; errors="replace" guards against stray bytes.
    return Path(path).read_text(errors="replace").splitlines()


# ------------------------------------------------------------------ GROUNDCHECK
@dataclass
class GroundCheckSet:
    set_name: str          # "G", "N", "N+AUTOSPC", "F", "A"
    matrix: str            # "KGG", "KNN", "KNN+AUTO", "KFF", "KAA1"
    limit: float           # strain energy limit printed by Nastran
    strain_energy: list    # 6 values, directions 1..6
    passed: list           # 6 bools

    @property
    def all_pass(self) -> bool:
        return len(self.passed) == 6 and all(self.passed)


_GC_HEAD = re.compile(r"RIGID BODY CHECKS OF MATRIX\s+(\S+)\s+\((.+?)-SET\)")
_GC_LIMIT = re.compile(r"AGAINST THE LIMIT OF\s+(" + _F + ")")
_GC_ROW = re.compile(r"^\s+([1-6])\s+(" + _F + r")\s+(PASS|FAIL)\b")


def parse_groundcheck(lines: list[str]) -> list[GroundCheckSet]:
    out = []
    for i, line in enumerate(lines):
        m = _GC_HEAD.search(line)
        if not m:
            continue
        limit, se, ok = float("nan"), [], []
        for j in range(i + 1, min(i + 20, len(lines))):
            ml = _GC_LIMIT.search(lines[j])
            if ml:
                limit = float(ml.group(1))
            mr = _GC_ROW.match(lines[j])
            if mr:
                se.append(float(mr.group(2)))
                ok.append(mr.group(3) == "PASS")
                if len(se) == 6:
                    break
        out.append(GroundCheckSet(m.group(2).strip(), m.group(1), limit, se, ok))
    return out


# ---------------------------------------------------------- WEIGHTCHECK / GPWG
@dataclass
class MassBlock:
    """One WEIGHTCHECK or grid point weight generator printout."""
    source: str                       # "WEIGHTCHECK" or "GPWG"
    dof_set: Optional[str] = None     # WEIGHTCHECK only: G, N, N+AUTOSPC, F, A, V
    reference_point: Optional[int] = None
    MO: list = field(default_factory=list)          # 6x6 rigid-body mass matrix about ref point
    change_pct: list = field(default_factory=list)  # 6 diagonal % changes vs G-set (reduced sets)
    mass: list = field(default_factory=list)        # mass in the S directions X, Y, Z
    cg: list = field(default_factory=list)          # 3x3 rows X, Y, Z -> (x, y, z) C.G.
    IS: list = field(default_factory=list)          # 3x3 inertia about CG, products printed +int(xy dm)
    IQ: list = field(default_factory=list)          # 3 principal inertias
    Q: list = field(default_factory=list)           # 3x3 principal directions


_MB_HEAD = re.compile(r"O U T P U T\s+F R O M\s+(W E I G H T\s+C H E C K|G R I D\s+P O I N T\s+W E I G H T)")
_MB_SET = re.compile(r"DEGREES OF FREEDOM SET\s*=\s*(\S+)")
_MB_REF = re.compile(r"REFERENCE POINT\s*=\s*(\d+)")
_MB_MASSROW = re.compile(r"^\s+([XYZ])\s+(" + _F + r")\s+(" + _F + r")\s+(" + _F + r")\s+(" + _F + r")")


def _star_lines(lines, start, n):
    """Raw text of the next n '* ... *' matrix rows at or after index start.
    Returns (row_strings, next_index). Blank lines between rows are skipped."""
    rows, j = [], start
    while j < len(lines) and len(rows) < n:
        s = lines[j].strip()
        if s.startswith("*") and s.endswith("*") and len(s) > 1:
            rows.append(s)
        j += 1
    return rows, j


def _star_rows(lines, start, n):
    """Like _star_lines, but each row parsed to its list of floats."""
    rows, j = _star_lines(lines, start, n)
    return [_floats(r) for r in rows], j


def parse_mass_blocks(lines: list[str]) -> list[MassBlock]:
    out = []
    i = 0
    while i < len(lines):
        m = _MB_HEAD.search(lines[i])
        if not m:
            i += 1
            continue
        blk = MassBlock("WEIGHTCHECK" if m.group(1).startswith("W E I G H T") else "GPWG")
        j = i + 1
        end = min(len(lines), i + 80)
        while j < end:
            line = lines[j]
            if _MB_HEAD.search(line):
                break  # next block starts
            ms = _MB_SET.search(line)
            mr = _MB_REF.search(line)
            st = line.strip()
            if ms:
                blk.dof_set = ms.group(1)
            elif mr:
                blk.reference_point = int(mr.group(1))
            elif st == "M O":
                blk.MO, j = _star_rows(lines, j + 1, 6)
                continue
            elif "CHANGE WITH RESPECT TO G-SET" in line:
                rows, j = _star_lines(lines, j + 1, 6)
                blk.change_pct = [_pct(r) for r in rows]
                continue
            elif "MASS AXIS SYSTEM" in line:
                for k in range(j + 1, min(j + 4, len(lines))):
                    mm = _MB_MASSROW.match(lines[k])
                    if mm:
                        blk.mass.append(float(mm.group(2)))
                        blk.cg.append([float(mm.group(3)), float(mm.group(4)), float(mm.group(5))])
                j += 4
                continue
            elif st == "I(S)":
                blk.IS, j = _star_rows(lines, j + 1, 3)
                continue
            elif st == "I(Q)":
                rows, j = _star_rows(lines, j + 1, 3)
                blk.IQ = [r[0] if r else float("nan") for r in rows]
                continue
            elif st == "Q":
                blk.Q, j = _star_rows(lines, j + 1, 3)
                continue
            elif st.startswith("NUMBER OF SOLVES REQUIRED"):
                break
            j += 1
        if blk.MO or blk.mass:
            out.append(blk)
        i = max(j, i + 1)
    return out


def _pct(line: str) -> float:
    """'*       0 %          *' -> 0.0 (the diagonal % entry on that row)."""
    m = re.search(r"(" + _F + r"|[-+]?\d+)\s*%", line)
    return float(m.group(1)) if m else float("nan")


# --------------------------------------------------------------- eigenvalues
@dataclass
class Mode:
    mode: int
    order: int
    eigenvalue: float
    radians: float
    cycles: float
    gen_mass: float
    gen_stiffness: float


_EIG_HEAD = re.compile(r"R E A L\s+E I G E N V A L U E S")
_EIG_ROW = re.compile(r"^\s+(\d+)\s+(\d+)" + _NUM * 5 + r"\s*$")
_STOP = re.compile(r"M O D A L|MODAL|O U T P U T|^\s*\*\*\*|R E S U L T A N T|RESULTANT|"
                   r"E I G E N V E C T O R|D I S P L A C E M E N T|END OF JOB")


def parse_eigenvalues(lines: list[str]) -> list[Mode]:
    modes: list[Mode] = []
    for i, line in enumerate(lines):
        if not _EIG_HEAD.search(line):
            continue
        for j in range(i + 1, len(lines)):
            m = _EIG_ROW.match(lines[j])
            if m:
                v = [float(x) for x in m.groups()[2:]]
                mode = Mode(int(m.group(1)), int(m.group(2)), *v)
                if not modes or mode.mode > modes[-1].mode:
                    modes.append(mode)
                continue
            if _STOP.search(lines[j]) and not _is_page_header(lines[j]):
                break
        # keep reading: a long table can restart under a repeated heading on the next page
    return modes


# ---------------------------------------------------- MEFFMASS mass fractions
@dataclass
class EffMassFraction:
    mode: int
    frequency: float
    fraction: dict   # dir -> fraction of total mass in that mode
    cumsum: dict     # dir -> running sum


_MEF_HEAD = re.compile(r"^\s*MODAL EFFECTIVE MASS FRACTION\s*$")
_MEF_DIRS = re.compile(r"MODE\s+FREQUENCY\s+([TR][123])\s+([TR][123])\s+([TR][123])")
_MEF_ROW = re.compile(r"^\s+(\d+)\s+(" + _F + r")" + _NUM * 6 + r"\s*$")


def parse_meffmass_fractions(lines: list[str]) -> tuple[list[EffMassFraction], dict]:
    """Returns (per-mode fractions, total fraction per direction from the summary)."""
    by_mode: dict[int, EffMassFraction] = {}
    for i, line in enumerate(lines):
        if not _MEF_HEAD.match(line):
            continue
        dirs = None
        for j in range(i + 1, len(lines)):
            md = _MEF_DIRS.search(lines[j])
            if md and dirs is None:
                dirs = md.groups()
                continue
            mr = _MEF_ROW.match(lines[j])
            if mr and dirs:
                mode = int(mr.group(1))
                vals = [float(x) for x in mr.groups()[2:]]
                rec = by_mode.setdefault(mode, EffMassFraction(mode, float(mr.group(2)), {}, {}))
                for k, d in enumerate(dirs):
                    rec.fraction[d] = vals[2 * k]
                    rec.cumsum[d] = vals[2 * k + 1]
                continue
            if dirs and (_is_page_header(lines[j]) or _MEF_HEAD.match(lines[j])
                         or _STOP.search(lines[j])):
                break
    totals = {}
    for i, line in enumerate(lines):
        if "TOTAL EFFECTIVE MASS FRACTION" in line:
            for j in range(i + 1, min(i + 6, len(lines))):
                vals = _floats(lines[j])
                if len(vals) == 6:
                    totals = dict(zip(["T1", "T2", "T3", "R1", "R2", "R3"], vals))
                    break
            break
    return [by_mode[k] for k in sorted(by_mode)], totals


# ----------------------------------------------------- OLOAD/SPCFORCE resultants
_RES_HEAD = re.compile(r"^\s*0?\s+(OLOAD|SPCFORCE|MPCFORCE)\s+RESULTANT")
_RES_SUB = re.compile(r"^0?\s+(\d+)\s+FX\s")
_RES_TOT = re.compile(r"^\s+TOTALS" + _NUM * 6)
_RES_STOP = re.compile(r"^\s*\*\*\*|^\s*\^\^\^|EPSILON|O U T P U T|D I S P L A C E M E N T|"
                       r"MESSAGE|END OF JOB")


def parse_resultants(lines: list[str], kind: str) -> dict[int, list[float]]:
    """kind: 'OLOAD' or 'SPCFORCE'. Returns {subcase: [T1, T2, T3, R1, R2, R3] TOTALS}.

    Totals are about the origin of the basic system, in basic coordinates (the .f06
    says so in the line above the table)."""
    out: dict[int, list[float]] = {}
    for i, line in enumerate(lines):
        m = _RES_HEAD.match(line)
        if not m or m.group(1) != kind:
            continue
        sub = None
        for j in range(i + 1, len(lines)):
            s = lines[j]
            ms = _RES_SUB.match(s)
            if ms:
                sub = int(ms.group(1))
                continue
            mt = _RES_TOT.match(s)
            if mt and sub is not None:
                out[sub] = [float(x) for x in mt.groups()]
                continue
            mh = _RES_HEAD.match(s)
            if mh:
                break  # another resultant table begins; the outer loop handles it
            if _RES_STOP.search(s):
                break
    return out


# -------------------------------------------------------------------- EPSILON
_EPS_HEAD = re.compile(r"LOAD SEQ\. NO\.\s+EPSILON")
_EPS_ROW = re.compile(r"^\s+(\d+)\s+(" + _F + r")\s+(" + _F + r")\s*(\*+)?\s*$")


def parse_epsilon(lines: list[str]) -> list[dict]:
    """[{seq, epsilon, external_work, flagged}] in the order printed (= subcase order)."""
    out = []
    for i, line in enumerate(lines):
        if not _EPS_HEAD.search(line):
            continue
        started = False
        for j in range(i + 1, min(i + 400, len(lines))):
            m = _EPS_ROW.match(lines[j])
            if m:
                started = True
                out.append(dict(seq=int(m.group(1)), epsilon=float(m.group(2)),
                                external_work=float(m.group(3)), flagged=bool(m.group(4))))
            elif started and (lines[j].strip() == "" or _is_page_header(lines[j])
                              or _STOP.search(lines[j])):
                break
    return out


# ------------------------------------------------- singularities and messages
_SING = re.compile(r"\*\*\*\s+(\d+)\s+SINGULARITIES FOUND\s+(\d+)\s+SINGULARITIES ELIMINATED")


def parse_singularities(lines: list[str]) -> list[tuple[int, int]]:
    """[(found, eliminated)] per occurrence (AUTOSPC runs once per SPC set / subcase group)."""
    return [(int(m.group(1)), int(m.group(2)))
            for m in (_SING.search(x) for x in lines) if m]


_MSG = re.compile(r"\*\*\*\s+(USER|SYSTEM)\s+(FATAL|WARNING)\s+MESSAGE\s*(\d+)?\s*(\(\S+\))?")


@dataclass
class Message:
    kind: str       # "FATAL" or "WARNING"
    origin: str     # "USER" or "SYSTEM"
    number: str
    text: str
    line_no: int


def parse_messages(lines: list[str]) -> list[Message]:
    out = []
    for i, line in enumerate(lines):
        m = _MSG.search(line)
        if not m:
            continue
        body = [line.strip()]
        for j in range(i + 1, min(i + 8, len(lines))):
            s = lines[j]
            if s.strip() == "" or _is_page_header(s) or "***" in s:
                break
            body.append(s.strip())
        out.append(Message(m.group(2), m.group(1), m.group(3) or "", " ".join(body), i + 1))
    return out


def parse_version(lines: list[str]) -> str:
    for line in lines[:200]:
        m = re.search(r"VERSION\s+-\s+(\S+)", line)
        if m:
            return m.group(1)
    for line in lines[:400]:
        m = re.search(r"SIMCENTER NASTRAN\s+(\d+/\d+/\d+)", line)
        if m:
            return m.group(1)
    return "unknown"


def job_completed(lines: list[str]) -> bool:
    return any("END OF JOB" in x for x in lines[-60:])


# ------------------------------------------------------------- one-stop parse
@dataclass
class F06:
    path: str
    version: str
    completed: bool
    groundcheck: list
    mass_blocks: list
    modes: list
    meff_fractions: list
    meff_totals: dict
    oload: dict
    spcforce: dict
    epsilon: list
    singularities: list
    messages: list
    excerpts: dict = field(default_factory=dict)  # see extract_excerpts()

    @property
    def fatals(self) -> list:
        return [m for m in self.messages if m.kind == "FATAL"]


# ------------------------------------------------------------ verbatim excerpts
def _banner_lines(lines: list[str]) -> set[str]:
    """Title/subtitle/label lines Nastran repeats under every page header. Found as the
    non-blank lines right after page headers that recur on 3+ pages."""
    counts: dict[str, int] = {}
    for i, line in enumerate(lines):
        # Only the line directly under the header (the SUBTITLE/LABEL line, e.g.
        # "BD IRAD V2"). Table headings sit further down, after a "0" line, and must stay.
        if _is_page_header(line) and i + 1 < len(lines):
            st = lines[i + 1].strip()
            if st and st not in ("0",):
                counts[st] = counts.get(st, 0) + 1
    return {k for k, n in counts.items() if n >= 3}


def _clean(block: list[str], banner: set[str]) -> list[str]:
    """Strip page breaks, repeated banner lines and the column-1 carriage-control
    character ('0'/'1'), keep everything else exactly as Nastran printed it."""
    out = []
    for line in block:
        if _is_page_header(line) or line.strip() in banner or line.strip() in ("0", "1"):
            continue
        if line[:1] in ("0", "1"):
            line = " " + line[1:]
        line = line.rstrip()
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    # drop common leading indent so the block fits the page
    ind = min((len(x) - len(x.lstrip()) for x in out if x.strip()), default=0)
    return [x[ind:] for x in out]


def extract_excerpts(lines: list[str]) -> dict[str, list[list[str]]]:
    """Verbatim .f06 blocks for the report, so readers see the numbers exactly as Nastran
    printed them, alongside the tool's own tables.

    Keys -> list of blocks (each block = list of text lines):
      groundcheck   one block per DOF set (UIM 7570 header through the 6 PASS/FAIL rows)
      mass          one block per WEIGHTCHECK / grid point weight generator printout
      eigenvalues   the REAL EIGENVALUES table (all pages joined)
      meff          each MODAL EFFECTIVE MASS FRACTION table
      oload, spcforce   each resultant table (all subcases)
      epsilon       the UIM 5293 EPSILON table(s)"""
    banner = _banner_lines(lines)
    out: dict[str, list] = {k: [] for k in ("groundcheck", "mass", "eigenvalues", "meff",
                                            "oload", "spcforce", "epsilon")}
    n = len(lines)
    for i, line in enumerate(lines):
        if _GC_HEAD.search(line):
            a = i - 1 if i > 0 and "7570" in lines[i - 1] else i
            rows, b = 0, i
            for j in range(i + 1, min(i + 20, n)):
                if _GC_ROW.match(lines[j]):
                    rows += 1
                    b = j
                    if rows == 6:
                        break
            out["groundcheck"].append(_clean(lines[a:b + 1], banner))
        elif _MB_HEAD.search(line):
            b = i
            for j in range(i + 1, min(i + 90, n)):
                if j > i + 1 and _MB_HEAD.search(lines[j]):
                    break
                st = lines[j].strip()
                is_matrix_row = st.startswith("*") and st.endswith("*") and not st.startswith("***")
                if is_matrix_row or _MB_MASSROW.match(lines[j]) or "REFERENCE POINT" in st \
                        or "DEGREES OF FREEDOM SET" in st or st in ("M O", "S", "I(S)", "I(Q)", "Q") \
                        or "MASS AXIS" in st or "DIRECTION" == st or "CHANGE WITH" in st:
                    b = j
                elif st.startswith("NUMBER OF SOLVES") or "R E A L   E I G E N" in st:
                    break  # next section; the block ends at the last matrix row seen
            if b > i:
                out["mass"].append(_clean(lines[i:b + 1], banner))
        elif _EIG_HEAD.search(line):
            b = i
            for j in range(i + 1, n):
                if _EIG_ROW.match(lines[j]):
                    b = j
                elif _STOP.search(lines[j]) and not _is_page_header(lines[j]):
                    break
            out["eigenvalues"].append(_clean(lines[i:b + 1], banner))
        elif _MEF_HEAD.match(line):
            b = i
            for j in range(i + 1, n):
                if _MEF_ROW.match(lines[j]):
                    b = j
                elif j > i + 3 and (_is_page_header(lines[j]) or _STOP.search(lines[j])):
                    break
            out["meff"].append(_clean(lines[i:b + 1], banner))
        elif _RES_HEAD.match(line):
            kind = _RES_HEAD.match(line).group(1).lower()
            if kind not in out:
                continue
            a = i - 1 if i > 0 and "RESULTANTS ABOUT" in lines[i - 1] else i
            b = i
            for j in range(i + 1, n):
                if _RES_TOT.match(lines[j]) or re.match(r"^\s+(FX|FY|FZ|MX|MY|MZ)\s", lines[j]) \
                        or _RES_SUB.match(lines[j]):
                    b = j
                elif _RES_HEAD.match(lines[j]) or _RES_STOP.search(lines[j]):
                    break
            out[kind].append(_clean(lines[a:b + 1], banner))
        elif _EPS_HEAD.search(line):
            a = i
            for k in range(i - 1, max(i - 4, -1), -1):
                if "5293" in lines[k]:
                    a = k
                    break
            b = i
            for j in range(i + 1, min(i + 400, n)):
                if _EPS_ROW.match(lines[j]):
                    b = j
                elif b > i and (lines[j].strip() == "" or _is_page_header(lines[j]) or _STOP.search(lines[j])):
                    break
            out["epsilon"].append(_clean(lines[a:b + 1], banner))
    return out


def parse_f06(path) -> F06:
    lines = read_lines(path)
    meff, meff_tot = parse_meffmass_fractions(lines)
    return F06(
        path=str(path),
        version=parse_version(lines),
        completed=job_completed(lines),
        groundcheck=parse_groundcheck(lines),
        mass_blocks=parse_mass_blocks(lines),
        modes=parse_eigenvalues(lines),
        meff_fractions=meff,
        meff_totals=meff_tot,
        oload=parse_resultants(lines, "OLOAD"),
        spcforce=parse_resultants(lines, "SPCFORCE"),
        epsilon=parse_epsilon(lines),
        singularities=parse_singularities(lines),
        messages=parse_messages(lines),
        excerpts=extract_excerpts(lines),
    )
