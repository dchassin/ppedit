"""Tests for time series of case values and multistep OPF (issue #124;
the solves are skipped when cvxopf is not installed; fast, no network)"""

import base64
import importlib.util
import json

import pytest

from ppedit import Case
from ppedit.case import CaseError
from ppedit._tableio import read_series, dump_series

HAS_CVXOPF = importlib.util.find_spec("cvxopf") is not None
needs_cvxopf = pytest.mark.skipif(not HAS_CVXOPF,reason="cvxopf is not installed")

def _case9_series():
    """case9 with three steps: rising load at buses 5 and 7, gen 3 capped at step 3"""
    case = Case.builtin("case9")
    case.set_series(["bus.Pd.5","bus.Pd.7","bus.Qd.5","gen.Pmax.3"],
        [[90,100,30,270],[120,140,40,270],[150,160,50,60]],["00:00","01:00","02:00"])
    return case

def _b64(text):
    return base64.b64encode(text.encode()).decode()

def test_set_series_normalizes():
    """Column names are canonical, labels default to 1..T, delta is kept"""
    case = Case.builtin("case9")
    ser = case.set_series(["BUS.pd.5"," gen.PMAX.2 "],[[1,2],["3",4.5]],delta=0.25)
    assert ser == {"index":["1","2"],"delta":0.25,"columns":["bus.Pd.5","gen.Pmax.2"],
        "values":[[1.0,2.0],[3.0,4.5]]}
    assert case.series_values("bus","Pd")[1][4] == 3.0
    assert case.series_values("bus","Pd")[1][6] == case.tables["bus"][6][2]
    assert case.set_series(None) is None and case.series is None

@pytest.mark.parametrize("columns,values,index,delta,match",[
    (["bus.Vm.5"],[[1]],None,1,"can be Pd, Qd"),
    (["load.Pd.5"],[[1]],None,1,"not table.column.key"),
    (["bus.Pd.99"],[[1]],None,1,"bus 99 does not exist"),
    (["gen.Pmax.4"],[[1]],None,1,"gen row 4 does not exist"),
    (["bus.Pd.5","bus.pd.5"],[[1,2]],None,1,"repeated"),
    (["bus.Pd.5"],[],None,1,"no time steps"),
    (["bus.Pd.5"],[[1],[None]],None,1,"step 2.*not a finite number"),
    (["bus.Pd.5"],[[1,2]],None,1,"step 1 has 2 values"),
    (["bus.Pd.5"],[[1]],["a","b"],1,"2 step labels for 1 steps"),
    (["bus.Pd.5"],[[1]],None,0,"positive number of hours"),
])
def test_set_series_errors(columns,values,index,delta,match):
    """Bad columns, values, labels and step lengths are refused"""
    with pytest.raises(CaseError,match=match):
        Case.builtin("case9").set_series(columns,values,index,delta)

def test_series_follows_edits():
    """Bus renumbering renames columns; deleting and inserting rows renumbers them"""
    case = Case.builtin("case9")
    case.set_series(["bus.Pd.5","gen.Pmax.1","gen.Pmax.3"],[[1,2,3]])
    assert case.set_value("bus",4,"bus_i",55) is True
    assert case.series["columns"][0] == "bus.Pd.55"
    case.delete_rows("gen",[0])
    assert case.series["columns"] == ["bus.Pd.55","gen.Pmax.2"]
    assert case.series["values"] == [[1.0,3.0]]
    case.add_row("gen",after=-1)
    assert case.series["columns"] == ["bus.Pd.55","gen.Pmax.3"]
    case.delete_rows("bus",[4],cascade=True)
    assert case.series["columns"] == ["gen.Pmax.3"]

def test_at_step():
    """at_step returns a copy with the step's values and no series"""
    case = _case9_series()
    step = case.at_step(2)
    assert step.tables["bus"][4][2:4] == [150.0,50.0] and step.tables["gen"][2][8] == 60.0
    assert step.series is None and case.tables["bus"][4][2] == 90
    with pytest.raises(CaseError,match="no series step 4"):
        case.at_step(3)

@pytest.mark.parametrize("fmt",["py","json"])
def test_series_saved(fmt):
    """The series is written to and read from .py and .json cases"""
    case = _case9_series()
    case.set_series_delta(0.5)
    again = Case.load(f"x.{fmt}",case.dumps(fmt))
    assert again.series == case.series

def test_series_not_in_glm():
    """GLM output notes that the series is not written"""
    text = _case9_series().dumps("glm")
    assert "time series (3 steps, 4 columns) not written" in text
    assert Case.load("x.glm",text).series is None

def test_bad_series_in_file():
    """A series that does not match the case is a load error"""
    data = Case.builtin("case9").to_ppc()
    data["series"] = {"columns":["bus.Pd.99"],"values":[[1]]}
    with pytest.raises(CaseError,match="series: .*bus 99"):
        Case.load("x.json",json.dumps(data))

def test_read_series_labels():
    """The label column may be named time (any case) or be an unnamed index"""
    index,columns,rows = read_series("s.csv",b"Time,bus.Pd.5\n2026-01-01 00:00,90\n2026-01-01 01:00,95\n")
    assert index == ["2026-01-01 00:00","2026-01-01 01:00"] and columns == ["bus.Pd.5"]
    assert rows == [[90],[95]]
    index,columns,rows = read_series("s.csv",b",bus.Pd.5\n0,90\n")
    assert index == ["0"] and columns == ["bus.Pd.5"]
    assert read_series("s.csv",b"bus.Pd.5\n90\n") == (None,["bus.Pd.5"],[[90]])
    with pytest.raises(CaseError,match="2 step label columns"):
        read_series("s.csv",b"time,step,bus.Pd.5\n1,1,90\n")

@pytest.mark.parametrize("ext",[".csv",".json"])
def test_dump_series_round_trip(ext):
    """An exported series reads back unchanged"""
    case = _case9_series()
    index,columns,rows = read_series("s"+ext,dump_series(case.series,"s"+ext))
    again = Case.builtin("case9")
    again.set_series(columns,rows,index)
    assert again.series == case.series

def test_server_series(client,post):
    """Import, step length, export, undo and removal through the API"""
    text = "time,bus.Pd.5,bus.Pd.7\nh1,90,100\nh2,120,140\n"
    status,st = post("/api/series/import",{"filename":"load.csv","data":_b64(text),"delta":0.5})
    assert status == 200 and st["imported"] == {"steps":2,"columns":2}
    assert st["case"]["series"]["index"] == ["h1","h2"] and st["case"]["series"]["delta"] == 0.5
    status,st = post("/api/series",{"delta":2})
    assert st["case"]["series"]["delta"] == 2
    res = client.get("/api/series/export?format=csv")
    assert res.status_code == 200 and res.data.decode().startswith("time,bus.Pd.5,bus.Pd.7")
    status,st = post("/api/undo")
    assert st["case"]["series"]["delta"] == 0.5
    status,st = post("/api/series",{"clear":True})
    assert st["case"]["series"] is None and st["dirty"]
    status,st = post("/api/undo")
    assert st["case"]["series"]["columns"] == ["bus.Pd.5","bus.Pd.7"]
    status,st = post("/api/series/import",{"filename":"bad.csv","data":_b64("bus.Pd.99\n1\n")})
    assert status == 400 and "bus 99" in st["error"]
    status,st = post("/api/solve",{"kind":"opf","algorithm":"PIPS","multistep":True})
    assert status == 400 and "CVXOPF" in st["error"]

def test_multistep_needs_series():
    """Multistep OPF needs a series and a cvxopf solver"""
    case = Case.builtin("case9")
    with pytest.raises(CaseError,match="CVXOPF"):
        case.solve("opf","PIPS",multistep=True)
    if HAS_CVXOPF:
        with pytest.raises(CaseError,match="needs a time series"):
            case.solve("opf","CVXOPF-DC",multistep=True)

@needs_cvxopf
@pytest.mark.parametrize("algorithm",["CVXOPF-AC","CVXOPF-SOCP","CVXOPF-DC","CVXOPF-1NODE"])
def test_multistep_solves(algorithm):
    """Each step serves its own load, the gen limit series holds, and step
    costs add up to the horizon objective"""
    case = _case9_series()
    r = case.solve("opf",algorithm,multistep=True)
    assert r["success"] and r["multistep"] == {"steps":3,"delta":1.0,"index":["00:00","01:00","02:00"]}
    assert [s["label"] for s in r["steps"]] == ["00:00","01:00","02:00"]
    assert r["gen"] == r["steps"][0]["gen"] and "messages" not in r["steps"][0]
    pd = [sum(a["Pd"] for a in s["areas"]) for s in r["steps"]]
    assert pd == pytest.approx([315,385,435])
    pg = [sum(g["Pg"] for g in s["gen"]) for s in r["steps"]]
    assert all(b > a for a,b in zip(pg,pg[1:]))
    assert r["steps"][2]["gen"][2]["Pg"] <= 60 + 1e-3
    assert r["steps"][0]["gen"][2]["Pg"] > 60
    assert sum(s["objective"] for s in r["steps"]) == pytest.approx(r["horizon_objective"],rel=1e-4)
    assert "Step 3 of 3 (02:00)" in r["steps"][2]["log"]
    assert "multistep horizon: 3 step(s) of 1 h" in r["log"]

@needs_cvxopf
def test_multistep_flat_matches_single():
    """A flat series gives the single-step dispatch at every step; delta scales the horizon cost"""
    case = Case.builtin("case9")
    single = case.solve("opf","CVXOPF-DC")
    case.set_series(["bus.Pd.5"],[[90],[90]],delta=0.5)
    r = case.solve("opf","CVXOPF-DC",multistep=True)
    for step in r["steps"]:
        for a,b in zip(step["gen"],single["gen"]):
            assert a["Pg"] == pytest.approx(b["Pg"],abs=1e-2)
    assert r["horizon_objective"] == pytest.approx(single["objective"],rel=1e-4)

@needs_cvxopf
def test_multistep_dcline_limits():
    """DC line limits by step bound the DC line flow"""
    case = Case.builtin("case9")
    n = case.add_row("dcline",{"fbus":4,"tbus":9,"status":1,"Pmin":-50,"Pmax":50,"Vf":1,"Vt":1})
    case.set_series([f"dcline.Pmax.{n+1}",f"dcline.Pmin.{n+1}"],[[50,-50],[5,-5]])
    r = case.solve("opf","CVXOPF-DC",multistep=True)
    assert r["success"]
    assert abs(r["steps"][1]["dcline"][0]["Pf"]) <= 5 + 1e-3
    assert abs(r["steps"][0]["dcline"][0]["Pf"]) > 5

@needs_cvxopf
def test_multistep_refusals():
    """Pmin > Pmax at a step and per-step gen limits with the recommended preparation are refused"""
    case = Case.builtin("case9")
    case.set_series(["gen.Pmin.1"],[[0],[300]])
    with pytest.raises(CaseError,match="step 2.*gen 1 has Pmin 300 > Pmax 250"):
        case.solve("opf","CVXOPF-DC",multistep=True)
    case = _case9_series()
    with pytest.raises(CaseError,match="default cvxopf preparation"):
        case.solve("opf","CVXOPF-DC",{"preparation":"recommended"},multistep=True)
    case.set_series(["bus.Pd.5"],[[90],[150]])
    assert case.solve("opf","CVXOPF-DC",{"preparation":"recommended"},multistep=True)["success"]

@needs_cvxopf
def test_server_multistep_apply(post):
    """A multistep solve applies the chosen step"""
    text = "time,bus.Pd.5\na,90\nb,150\n"
    post("/api/series/import",{"filename":"load.csv","data":_b64(text)})
    status,st = post("/api/solve",{"kind":"opf","algorithm":"CVXOPF-DC","multistep":True})
    assert status == 200 and len(st["result"]["steps"]) == 2
    pg = [g["Pg"] for g in st["result"]["steps"][1]["gen"]]
    status,st = post("/api/apply",{"step":1})
    assert status == 200
    assert [g[1] for g in st["case"]["tables"]["gen"]] == pytest.approx(pg)
    status,st = post("/api/solve",{"kind":"opf","algorithm":"CVXOPF-DC","multistep":True})
    status,st = post("/api/apply",{"step":5})
    assert status == 400 and "no step 6" in st["error"]

# ----- panels: steps in rows, row numbers or geohashes in columns -----

def _geo_case():
    """case9 with buses 5 and 7 in one geohash cell and bus 9 in another"""
    case = Case.builtin("case9")
    case.geodata = [[5,37.77,-122.42],[7,37.771,-122.421],[9,34.05,-118.24],[1,40.0,-100.0]]
    case.geotags = []
    return case

def test_geohash():
    """Geohashes match the standard encoding"""
    from ppedit.case import geohash
    assert geohash(36.670897,-108.581419,6) == "9wdb95"
    assert geohash(57.64911,10.40744,11) == "u4pruydqqvj"
    assert geohash(-25.382708,-49.265506,8) == "6gkzwgjz"

def test_panel_rows():
    """Row-number columns set the chosen value; for buses the row, not the bus number"""
    case = Case.builtin("case9")
    rep = case.set_series_panel("bus","pd",["5","7.0"],[[1,2],[3,4]],["a","b"],0.5)
    assert rep == {"columns":2,"keys":2,"ignored":[],"unlocated":0}
    assert case.series == {"index":["a","b"],"delta":0.5,"columns":["bus.Pd.5","bus.Pd.7"],
        "values":[[1.0,2.0],[3.0,4.0]]}
    case.set_series_panel("gen","Pmax",["3"],[[10],[20]])
    assert case.series["columns"] == ["bus.Pd.5","bus.Pd.7","gen.Pmax.3"]
    assert case.series["index"] == ["a","b"] and case.series["delta"] == 0.5
    case.set_series_panel("bus","Pd",["5"],[[9],[9]])
    assert case.series["columns"] == ["bus.Pd.7","gen.Pmax.3","bus.Pd.5"]
    case.set_series_panel("bus","Qd",["5"],[[1],[1],[1]],merge=False)
    assert case.series["columns"] == ["bus.Qd.5"] and len(case.series["values"]) == 3

def test_panel_geohash_split():
    """A geohash cell's value is shared among its buses as chosen"""
    case = _geo_case()  # bus 5 Pd 90, bus 7 Pd 100, bus 9 Pd 125
    rep = case.set_series_panel("bus","Pd",["9q8yy","9q5c","dr5r"],[[190,50,1],[380,100,2]])
    assert rep == {"columns":3,"keys":2,"ignored":["dr5r"],"unlocated":5}
    vals = dict(zip(case.series["columns"],case.series["values"][1]))
    assert vals == pytest.approx({"bus.Pd.5":180,"bus.Pd.7":200,"bus.Pd.9":100})
    case.set_series_panel("bus","Pd",["9q8yy"],[[190],[380]],split="equal",merge=False)
    assert case.series["values"][0] == [95.0,95.0]
    case.set_series_panel("bus","Pd",["9q8yy"],[[190],[380]],split="copy",merge=False)
    assert case.series["values"][0] == [190.0,190.0]

def test_panel_geohash_gen():
    """Generators are located by their bus"""
    case = Case.builtin("case9")
    case.geodata,case.geotags = [[1,40.0,-100.0],[2,40.0,-100.0],[3,30.0,-90.0]],[]
    case.set_series_panel("gen","Pmax",["9z","dj"],[[300,100]],split="proportional")
    vals = dict(zip(case.series["columns"],case.series["values"][0]))
    assert vals == pytest.approx({"gen.Pmax.1":300*250/550,"gen.Pmax.2":300*300/550,"gen.Pmax.3":100})

@pytest.mark.parametrize("args,kwargs,match",[
    (("load","Pd",["1"],[[1]]),{},"can set bus, gen, dcline"),
    (("bus","Vm",["1"],[[1]]),{},"can be Pd, Qd"),
    (("bus","Pd",["10"],[[1]]),{},"bus row 10 does not exist"),
    (("bus","Pd",["1","x"],[[1,2]]),{"keys_are":"row"},"not row numbers"),
    (("bus","Pd",["9q8!"],[[1]]),{},"not geohashes"),
    (("bus","Pd",["9q8yy"],[[1]]),{},"need bus geodata"),
    (("bus","Pd",["1","1"],[[1,2]]),{},"repeated"),
    (("bus","Pd",["1"],[[None]]),{},"step 1, column 1"),
    (("bus","Pd",["1"],[]),{},"no time steps"),
    (("bus","Pd",["1"],[[1]]),{"split":"half"},"split must be"),
])
def test_panel_errors(args,kwargs,match):
    """Bad tables, values, keys and options are refused"""
    with pytest.raises(CaseError,match=match):
        Case.builtin("case9").set_series_panel(*args,**kwargs)

def test_panel_geohash_errors():
    """Nested cells, no match and a merge with other steps are refused"""
    case = _geo_case()
    with pytest.raises(CaseError,match="in panel cells 9q8 and 9q8yy"):
        case.set_series_panel("bus","Pd",["9q8","9q8yy"],[[1,2]])
    with pytest.raises(CaseError,match="no panel column matched a located bus"):
        case.set_series_panel("bus","Pd",["u4pr"],[[1]])
    case.set_series_panel("bus","Pd",["9q8yy"],[[1],[2]])
    with pytest.raises(CaseError,match="3 steps but the series has 2"):
        case.set_series_panel("bus","Pd",["9q8yy"],[[1],[2],[3]])

def test_server_panel(post):
    """A panel imports through the API, merges, and is undoable"""
    status,st = post("/api/series/import",{"filename":"p.csv","data":_b64("time,5,7\nh1,90,100\nh2,95,105\n"),
        "panel":{"table":"bus","column":"Pd"}})
    assert status == 200 and st["imported"]["keys"] == 2 and st["imported"]["columns"] == 2
    status,st = post("/api/series/import",{"filename":"g.csv","data":_b64("time,3\nh1,200\nh2,100\n"),
        "panel":{"table":"gen","column":"Pmax","keys":"row","merge":True}})
    assert st["case"]["series"]["columns"] == ["bus.Pd.5","bus.Pd.7","gen.Pmax.3"]
    assert st["case"]["series"]["index"] == ["h1","h2"]
    status,st = post("/api/undo")
    assert st["case"]["series"]["columns"] == ["bus.Pd.5","bus.Pd.7"]
    status,st = post("/api/series/import",{"filename":"p.csv","data":_b64("5\n1\n"),"panel":{"table":"bus"}})
    assert status == 400 and "table and column" in st["error"]
