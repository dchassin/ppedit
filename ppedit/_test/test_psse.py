"""Tests for PSS/E RAW input (`ppedit._psse`, `Case.load`, API, CLI); fast, no network"""

import math
import os
import re

import pytest

from ppedit import Case, CaseError, main
from ppedit._psse import _fields

DATA = os.path.join(os.path.dirname(__file__),"data")
V33 = os.path.join(DATA,"psse_v33.raw")
V35 = os.path.join(DATA,"psse_v35.raw")

@pytest.mark.parametrize("line,fields",[
    ("1,'A B', 2.5 ,,3 / comment, 4",["1","A B","2.5","","3"]),
    ("  1 'X'   2",["1","X","2"]),
    ("1,\"it's\",'say ''hi''',/",["1","it's","say 'hi'"]),
    (",1",["","1"]),
    ("1, 'a/b' ,2",["1","a/b","2"]),
])
def test_fields(line,fields):
    assert _fields(line) == fields

def test_v33_buses():
    case = Case.load(V33)
    bus = {r[0]:r for r in case.tables["bus"]}
    assert sorted(bus) == [1,2,3,4,11]
    # branch shunts (GI,BI,GJ,BJ pu) of the in-service branch only
    assert bus[1][4:6] == pytest.approx([1.0,2.0])
    assert bus[2][4:6] == pytest.approx([0.0,3.0])
    # in-service load: PL + IP*VM + YP*VM^2, QL + IQ*VM - YQ*VM^2; fixed shunt
    assert bus[3][2:6] == pytest.approx([50+10*0.98+4*0.98**2,20+5*0.98-2*0.98**2,1.0,10.0])
    # in-service switched shunt BINIT; default voltage limits
    assert bus[4][5] == pytest.approx(5.0)
    assert bus[4][11:13] == [1.1,0.9] and bus[3][11:13] == [1.07,0.93]
    assert bus[3][6] == 2 and bus[3][9] == 115.0
    # star bus: next power of ten, winding 1 base kV and area, VMSTAR/ANSTAR
    assert bus[11][:2] == [11,1] and bus[11][7:10] == pytest.approx([0.99,-2.0,230.0])
    assert any("star point" in x for x in case.notes)
    assert any("zone" in x and "owner" in x for x in case.notes)

def test_v33_generators():
    gen = Case.load(V33).tables["gen"]
    assert gen[0][:10] == [1,100.0,10.0,200.0,-100.0,1.02,150.0,1,300.0,0.0]
    assert gen[1][:10] == [2,50.0,0.0,50.0,-50.0,1.01,80.0,0,100.0,10.0]

def test_v33_branches():
    br = Case.load(V33).tables["branch"]
    assert len(br) == 6
    assert br[0] == [1,2,0.01,0.05,0.1,200.0,220.0,240.0,0.0,0.0,1,-360.0,360.0]
    assert br[1][1] == 2 and br[1][10] == 0       # negative J (metered end) and status
    # 2-winding: CW=2 (kV), CZ=2 on SBASE 50 MVA, NOMV 0 = bus base
    assert br[2][:10] == pytest.approx([2,3,0.02,0.2,0,100,110,120,(235/230)/(115/115),-5])
    # 3-winding star equivalent; STAT=3 takes winding 3 out
    r12,x12,r23,x23,r31,x31 = 0.01,0.1,0.02,0.25,0.03,0.3
    star = [[1,(r12+r31-r23)/2,(x12+x31-x23)/2,1.05,0,1],
            [3,(r12+r23-r31)/2,(x12+x23-x31)/2,1.0,0,1],
            [4,(r31+r23-r12)/2,(x31+x23-x12)/2,0.95,30,0]]
    for row,(fbus,r,x,tap,shift,on) in zip(br[3:],star):
        assert row[:2] == [fbus,11] and row[10] == on
        assert row[2:4] + row[8:10] == pytest.approx([r,x,tap,shift])

def test_v33_dcline():
    dc = Case.load(V33).tables["dcline"]
    p = 100.0
    def qmax(amax):
        return p*math.tan(math.acos(0.5*(math.cos(math.radians(amax))+0.5)))
    assert len(dc) == 1
    assert dc[0][:3] == [1,2,1]
    assert dc[0][3:] == pytest.approx([p,p,0,0,1.02,1.01,0.85*p,1.15*p,
        p*math.tan(math.radians(5)),qmax(20),p*math.tan(math.radians(15)),qmax(25),0,0])

def test_v33_solves():
    case = Case.load(V33)
    assert not [x for x in case.validate() if x["level"] == "error"]
    assert case.solve("pf")["success"]

def test_v35():
    case = Case.load(V35)
    assert case.size()["bus"] == 3
    assert case.tables["gen"][0][:10] == [1,80.0,5.0,100.0,-100.0,1.0,120.0,1,150.0,20.0]
    br = case.tables["branch"]
    assert br[0][:8] == pytest.approx([1,2,0.002,0.02,0.05,300,310,320])
    assert [r[:4]+[r[10]] for r in br[1:]] == [[2,3,0.0,0.0002,1],[1,3,0.0,0.0002,0]]
    assert case.tables["bus"][1][5] == 25.0 and case.tables["bus"][2][2:4] == [80.0,30.0]
    assert any("switching device" in x for x in case.notes)
    assert case.solve("pf")["success"]

def test_round_trip_and_read_only(tmp_path):
    case = Case.load(V33)
    for ext in (".py",".json",".glm"):
        again = Case.load(str(tmp_path/f"x{ext}"),case.dumps(ext[1:]))
        for table in ("bus","gen","branch","dcline"):
            for a,b in zip(again.tables[table],case.tables[table],strict=True):
                assert a == pytest.approx(b,rel=1e-9)
    with pytest.raises(CaseError,match="read only"):
        case.save(str(tmp_path/"x.raw"))

@pytest.mark.parametrize("text,message",[
    ("not\na\nraw\n","SBASE"),
    ("0, 100, 33\nx\ny\n0 / END OF BUS DATA\n","no bus data"),
    ("0, 100, 21\nx\ny\n","revision 21"),
    ("0,100,33\nx\ny\n1,'A',230,3,1,1,1,1,0\n0 / END OF BUS DATA\n0\n0\n2,'1',10,0,9,-9,1,0,100\n",
        "bus 2"),
])
def test_errors(text,message):
    with pytest.raises(CaseError,match=message):
        Case.load("bad.raw",text)

def test_api_and_cli(post,tmp_path,capsys):
    with open(V35,encoding="utf-8") as fh:
        status,state = post("/api/open",{"filename":"psse_v35.raw","text":fh.read()})
    assert status == 200 and state["case"]["tables"]["bus"][0][0] == 1
    source = state["source"]
    assert source["file"] == "psse_v35.raw" and source["format"] == "raw"
    assert source["revision"] == 35 and source["size"]["bus"] == 3
    assert any("switching device" in x for x in source["notes"])
    status,state = post("/api/open",{"path":V35})
    assert status == 200 and state["format"] == "raw"
    status,err = post("/api/save",{})
    assert status == 400 and "read only" in err["error"]
    status,state = post("/api/save",{"path":str(tmp_path/"v35.glm")})
    assert status == 200 and not state["case"]["notes"]
    assert state["source"]["notes"]     # the load report stays after saving
    status,state = post("/api/new",{})
    assert state["source"] is None
    out = str(tmp_path/"v33.glm")
    assert main(["convert",V33,out]) == 0
    assert "star point" in capsys.readouterr().err
    assert len(Case.load(out).tables["bus"]) == 5

WECC240 = os.path.join(os.path.dirname(__file__),"..","..","examples","wecc240_psse.raw")

def _reproduces(case,vm_tol=1e-3,va_tol=0.01):
    """PF reproduces the solved voltages stored in the case"""
    result = case.solve("pf")
    assert result["success"]
    bus = {r[0]:r for r in case.tables["bus"]}
    assert max(abs(x["Vm"]-bus[x["bus_i"]][7]) for x in result["bus"]) < vm_tol
    assert max(abs(x["Va"]-bus[x["bus_i"]][8]) for x in result["bus"]) < va_tol

def test_wecc240():
    """Revision 34 (NREG at the end of generator records, @! headers) and
    remote voltage regulation"""
    case = Case.load(WECC240)
    assert case.size()["bus"] == 243 and case.size()["gen"] == 146
    bus = {r[0]:r for r in case.tables["bus"]}
    gen = case.tables["gen"][0]
    # 1032 'C' regulates bus 1002 at VS = 1.014: Vg is bus 1032's own voltage
    assert gen[:10] == [1032,712.0,-3.332,200.0,-200.0,bus[1032][7],873.0,1,873.0,0.0]
    assert all(g[8] >= g[9] and g[6] > 0 for g in case.tables["gen"])
    assert any("IREG" in x for x in case.notes)
    assert not [x for x in case.validate() if x["level"] == "error"]
    _reproduces(case)

@pytest.mark.parametrize("name,exact",[("IEEE_14_bus",True),("IEEE_24_bus",False),
    ("IEEE_30_bus",False),("IEEE_39_bus",True),("IEEE_57_bus",True),("IEEE_118_bus",False),
    ("IEEE_300_bus",False)])
def test_ieee_cases(name,exact):
    """Revision 33 cases solve; those without generators held at a reactive
    power limit reproduce their stored solution"""
    case = Case.load(os.path.join(DATA,f"{name}.raw"))
    assert not [x for x in case.validate() if x["level"] == "error"]
    assert exact != any("reactive power limit" in x for x in case.notes)
    if exact:
        _reproduces(case)
    else:
        assert case.solve("pf")["success"]

# MATPOWER's psse2mpc test cases and expected conversions (optional: needs the
# `matpower` package, whose test data is not redistributed here)

def _mfile(path):
    with open(path,encoding="utf-8") as fh:
        text = fh.read()
    out = {}
    for key in ("bus","gen","branch","dcline"):
        match = re.search(rf"mpc\.{key} = \[(.*?)\];",text,re.S)
        if match:
            out[key] = [[float(x) for x in line.strip().rstrip(";").split()]
                for line in match.group(1).strip().splitlines() if line.strip()]
    return out

@pytest.mark.parametrize("name",["t_psse_case2","t_psse_case3","t_case9_save2psse"])
def test_matpower_cases(name):
    matpower = pytest.importorskip("matpower")
    folder = os.path.join(matpower.path_matpower,"lib","t")
    if not os.path.exists(os.path.join(folder,f"{name}.raw")):
        pytest.skip("matpower test data not installed")
    case = Case.load(os.path.join(folder,f"{name}.raw"))
    expected = _mfile(os.path.join(folder,f"{name}.m"))
    stars = {r[0] for r in case.tables["bus"] if r[0] >= 100000}
    for table,rows in expected.items():
        got = case.tables[table]
        assert len(got) == len(rows),table
        for a,b in zip(got,rows):
            if table == "bus" and a[0] in stars:
                a = a[:9] + [1.0] + a[10:]  # psse2mpc gives star buses 1 kV
            if table == "gen" and abs(a[5]-b[5]) > 1e-4:
                # remote regulation: Vg is the generator bus voltage, not VS
                assert a[5] == next(r[7] for r in case.tables["bus"] if r[0] == a[0])
                a = a[:5] + [b[5]] + a[6:]
            assert a[:len(b)] == pytest.approx(b[:len(a)],rel=1e-4,abs=1e-4),(table,a,b)
