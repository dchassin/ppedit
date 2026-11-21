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

def _ser(st,name=None):
    """The series `name` (the only one when `None`) of an editor state"""
    series = st["case"]["series"]
    if name is None:
        assert len(series) == 1
        return series[0]
    return next(x for x in series if x["name"] == name)

def test_set_series_normalizes():
    """Column names are canonical, labels default to 1..T, delta is kept"""
    case = Case.builtin("case9")
    ser = case.set_series(["BUS.pd.5"," gen.PMAX.2 "],[[1,2],["3",4.5]],delta=0.25)
    assert ser == {"name":"series","index":["1","2"],"timed":False,"delta":0.25,
        "columns":["bus.Pd.5","gen.Pmax.2"],"values":[[1.0,2.0],[3.0,4.5]]}
    assert case.series_values("bus","Pd")[1][4] == 3.0
    assert case.series_values("bus","Pd")[1][6] == case.tables["bus"][6][2]
    assert case.set_series(None) is None and case.series == []

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
    assert case.get_series()["columns"][0] == "bus.Pd.55"
    case.delete_rows("gen",[0])
    assert case.get_series()["columns"] == ["bus.Pd.55","gen.Pmax.2"]
    assert case.get_series()["values"] == [[1.0,3.0]]
    case.add_row("gen",after=-1)
    assert case.get_series()["columns"] == ["bus.Pd.55","gen.Pmax.3"]
    case.delete_rows("bus",[4],cascade=True)
    assert case.get_series()["columns"] == ["gen.Pmax.3"]
    case.delete_rows("gen",[2])
    assert case.series == []

def test_at_step():
    """at_step returns a copy with the step's values and no series"""
    case = _case9_series()
    step = case.at_step(2)
    assert step.tables["bus"][4][2:4] == [150.0,50.0] and step.tables["gen"][2][8] == 60.0
    assert step.series == [] and case.tables["bus"][4][2] == 90
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
    assert "1 time series (series: 3 steps, 4 columns) not written" in text
    assert Case.load("x.glm",text).series == []

def test_bad_series_in_file():
    """A series that does not match the case is a load error"""
    data = Case.builtin("case9").to_ppc()
    data["series"] = {"columns":["bus.Pd.99"],"values":[[1]]}
    with pytest.raises(CaseError,match="series 'series': .*bus 99"):
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
    index,columns,rows = read_series("s"+ext,dump_series(case.get_series(),"s"+ext))
    again = Case.builtin("case9")
    again.set_series(columns,rows,index)
    assert again.series == case.series

def test_server_series(client,post):
    """Import, step length, export, undo and removal through the API"""
    text = "time,bus.Pd.5,bus.Pd.7\nh1,90,100\nh2,120,140\n"
    status,st = post("/api/series/import",{"filename":"load.csv","data":_b64(text),"delta":0.5})
    assert status == 200 and st["imported"] == {"name":"load","replaced":False,"steps":2,"columns":2}
    assert _ser(st)["index"] == ["h1","h2"] and _ser(st)["delta"] == 0.5
    status,st = post("/api/series",{"delta":2})
    assert _ser(st)["delta"] == 2
    res = client.get("/api/series/export?format=csv")
    assert res.status_code == 200 and res.data.decode().startswith("time,bus.Pd.5,bus.Pd.7")
    status,st = post("/api/undo")
    assert _ser(st)["delta"] == 0.5
    status,st = post("/api/series",{"clear":True})
    assert st["case"]["series"] == [] and st["dirty"]
    status,st = post("/api/undo")
    assert _ser(st)["columns"] == ["bus.Pd.5","bus.Pd.7"]
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
    assert r["success"] and r["multistep"] == {"steps":3,"delta":1.0,"index":["00:00","01:00","02:00"],
        "series":["series"]}
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
    assert case.get_series() == {"name":"series","index":["a","b"],"timed":True,"delta":0.5,
        "columns":["bus.Pd.5","bus.Pd.7"],"values":[[1.0,2.0],[3.0,4.0]]}
    case.set_series_panel("gen","Pmax",["3"],[[10],[20]])
    assert case.get_series()["columns"] == ["bus.Pd.5","bus.Pd.7","gen.Pmax.3"]
    assert case.get_series()["index"] == ["a","b"] and case.get_series()["delta"] == 0.5
    case.set_series_panel("bus","Pd",["5"],[[9],[9]])
    assert case.get_series()["columns"] == ["bus.Pd.7","gen.Pmax.3","bus.Pd.5"]
    case.set_series_panel("bus","Qd",["5"],[[1],[1],[1]],merge=False)
    assert case.get_series()["columns"] == ["bus.Qd.5"] and len(case.get_series()["values"]) == 3

def test_panel_geohash_split():
    """A geohash cell's value is shared among its buses as chosen"""
    case = _geo_case()  # bus 5 Pd 90, bus 7 Pd 100, bus 9 Pd 125
    rep = case.set_series_panel("bus","Pd",["9q8yy","9q5c","dr5r"],[[190,50,1],[380,100,2]])
    assert rep == {"columns":3,"keys":2,"ignored":["dr5r"],"unlocated":5}
    vals = dict(zip(case.get_series()["columns"],case.get_series()["values"][1]))
    assert vals == pytest.approx({"bus.Pd.5":180,"bus.Pd.7":200,"bus.Pd.9":100})
    case.set_series_panel("bus","Pd",["9q8yy"],[[190],[380]],split="equal",merge=False)
    assert case.get_series()["values"][0] == [95.0,95.0]
    case.set_series_panel("bus","Pd",["9q8yy"],[[190],[380]],split="copy",merge=False)
    assert case.get_series()["values"][0] == [190.0,190.0]

def test_panel_geohash_gen():
    """Generators are located by their bus"""
    case = Case.builtin("case9")
    case.geodata,case.geotags = [[1,40.0,-100.0],[2,40.0,-100.0],[3,30.0,-90.0]],[]
    case.set_series_panel("gen","Pmax",["9z","dj"],[[300,100]],split="proportional")
    vals = dict(zip(case.get_series()["columns"],case.get_series()["values"][0]))
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
    with pytest.raises(CaseError,match="3 steps but series 'series' has 2"):
        case.set_series_panel("bus","Pd",["9q8yy"],[[1],[2],[3]])

def test_server_panel(post):
    """A panel imports through the API, merges, and is undoable"""
    status,st = post("/api/series/import",{"filename":"p.csv","data":_b64("time,5,7\nh1,90,100\nh2,95,105\n"),
        "panel":{"table":"bus","column":"Pd"}})
    assert status == 200 and st["imported"]["keys"] == 2 and st["imported"]["columns"] == 2
    status,st = post("/api/series/import",{"filename":"g.csv","data":_b64("time,3\nh1,200\nh2,100\n"),
        "panel":{"table":"gen","column":"Pmax","keys":"row","merge":True},"name":"p"})
    assert _ser(st,"p")["columns"] == ["bus.Pd.5","bus.Pd.7","gen.Pmax.3"]
    assert _ser(st,"p")["index"] == ["h1","h2"]
    status,st = post("/api/undo")
    assert _ser(st,"p")["columns"] == ["bus.Pd.5","bus.Pd.7"]
    status,st = post("/api/series/import",{"filename":"p.csv","data":_b64("5\n1\n"),"panel":{"table":"bus"}})
    assert status == 400 and "table and column" in st["error"]

def test_server_dropna(post):
    """dropna drops missing values from series, panel and table imports"""
    text = "time,bus.Pd.5,bus.Pd.7\nh1,90,100\nh2,,140\nh3,120,\n"
    status,st = post("/api/series/import",{"filename":"s.csv","data":_b64(text)})
    assert status == 400 and "not a finite number" in st["error"]
    status,st = post("/api/series/import",{"filename":"s.csv","data":_b64(text),"dropna":"rows"})
    assert status == 200 and _ser(st,"s")["index"] == ["h1"]
    assert st["imported"]["dropped"] == {"rows":2,"columns":[]}
    status,st = post("/api/series/import",{"filename":"p.csv","data":_b64("time,5,7\na,1,\nb,2,\n"),
        "panel":{"table":"bus","column":"Pd","merge":False},"dropna":"columns"})
    assert status == 200 and _ser(st,"p")["columns"] == ["bus.Pd.5"]
    assert st["imported"]["dropped"] == {"rows":0,"columns":["7"]}
    status,st = post("/api/series/import",{"filename":"s.csv","data":_b64(text),"dropna":"bad"})
    assert status == 400 and "dropna must be one of" in st["error"]
    status,st = post("/api/table/import",{"table":"bus","filename":"b.csv",
        "data":_b64("bus_i,type,Pd\n100,1,5\n,,\n"),"dropna":"empty-rows"})
    assert status == 200 and st["imported"]["added"] == 1
    assert st["imported"]["dropped"] == {"rows":1,"columns":[]}

def test_server_ranges(post):
    """Row and date ranges select what series, panel and table imports read"""
    text = "time,bus.Pd.5\n2026-07-01 00:00,90\n2026-07-01 01:00,95\n2026-07-02 00:00,99\n"
    status,st = post("/api/series/import",{"filename":"s.csv","data":_b64(text),"end":"2026-07-01"})
    assert status == 200 and len(_ser(st,"s")["values"]) == 2
    assert st["imported"]["selected"] == {"rows":2,"of":3}
    status,st = post("/api/series/import",{"filename":"p.csv","data":_b64("time,5\na,1\nb,2\nc,3\n"),
        "panel":{"table":"bus","column":"Pd","merge":False},"range":"2-3"})
    assert _ser(st,"p")["index"] == ["b","c"]
    status,st = post("/api/series/import",{"filename":"s.csv","data":_b64(text),"range":"x"})
    assert status == 400 and "row range" in st["error"]
    status,st = post("/api/table/import",{"table":"bus","filename":"b.csv",
        "data":_b64("bus_i,type,Pd\n100,1,5\n101,1,6\n102,1,7\n"),"range":"2"})
    assert status == 200 and st["imported"]["added"] == 1
    assert st["case"]["tables"]["bus"][-1][0] == 101

def test_server_scale(post):
    """Scale and offset apply to series files and to panels before sharing"""
    status,st = post("/api/series/import",{"filename":"s.csv","data":_b64("time,bus.Pd.5\na,90000\nb,95000\n"),
        "scale":0.001})
    assert _ser(st,"s")["values"] == [[90.0],[95.0]]
    assert st["imported"]["scaled"] == {"scale":0.001,"offset":0.0}
    status,st = post("/api/series/import",{"filename":"p.csv","data":_b64("time,5\na,10\nb,20\n"),
        "panel":{"table":"bus","column":"Pd","merge":False},"scale":2,"offset":5})
    assert _ser(st,"p")["values"] == [[25.0],[45.0]]
    status,st = post("/api/series/import",{"filename":"s.csv","data":_b64("bus.Pd.5\n1\n"),"offset":"x"})
    assert status == 400 and "offset must be a finite number" in st["error"]

# ----- several series, combined for multistep OPF -----

def test_series_names():
    """Series are added, replaced and removed by name"""
    case = Case.builtin("case9")
    case.set_series(["bus.Pd.5"],[[1],[2]],name="load")
    case.set_series(["gen.Pmax.3"],[[100],[90]],name="cap")
    assert [x["name"] for x in case.series] == ["load","cap"]
    case.set_series(["bus.Pd.7"],[[3],[4]],name="load")
    assert [x["name"] for x in case.series] == ["load","cap"]
    assert case.get_series("load")["columns"] == ["bus.Pd.7"] and case.get_series() is None
    case.set_series(None,name="load")
    assert [x["name"] for x in case.series] == ["cap"]
    with pytest.raises(CaseError,match="1 to 64 printable"):
        case.set_series(["bus.Pd.5"],[[1]],name=" ")
    case.set_series(["bus.Pd.5"],[[1],[2]],name="x")
    with pytest.raises(CaseError,match="name the series"):
        case.set_series_delta(2)

def test_combined_by_step():
    """Series without time labels combine step by step"""
    case = Case.builtin("case9")
    case.set_series(["bus.Pd.5"],[[1],[2]],name="load")
    case.set_series(["gen.Pmax.3"],[[100],[90]],name="cap")
    ser = case.combined_series()
    assert ser["aligned"] == "step" and ser["columns"] == ["bus.Pd.5","gen.Pmax.3"]
    assert ser["values"] == [[1.0,100.0],[2.0,90.0]] and ser["index"] == ["1","2"]
    assert case.to_dict()["series_combined"]["steps"] == 2
    case.set_series(["gen.Pmax.3"],[[100],[90],[80]],name="cap")
    with pytest.raises(CaseError,match="same number of steps"):
        case.combined_series()
    assert "same number of steps" in case.to_dict()["series_combined"]["error"]

def test_combined_by_time():
    """Series with time labels combine at the times common to all, read as ISO dates"""
    case = Case.builtin("case9")
    case.set_series(["bus.Pd.5"],[[1],[2],[3]],["2026-07-01 00:00","2026-07-01 01:00","2026-07-01 02:00"],name="load")
    case.set_series(["gen.Pmax.3"],[[90],[80],[70]],["2026-07-01T01:00","2026-07-01T02:00","2026-07-01T03:00"],name="cap")
    ser = case.combined_series()
    assert ser["aligned"] == "time"
    assert ser["index"] == ["2026-07-01 01:00","2026-07-01 02:00"]
    assert ser["values"] == [[2.0,90.0],[3.0,80.0]]
    assert ser["used"] == {"load":2,"cap":2}
    case.set_series(["gen.Pmax.3"],[[1]],["2027-01-01"],name="cap")
    with pytest.raises(CaseError,match="no time in common"):
        case.combined_series()

def test_combined_text_labels():
    """Time labels that are not dates are matched as text"""
    case = Case.builtin("case9")
    case.set_series(["bus.Pd.5"],[[1],[2]],["a","b"],name="load")
    case.set_series(["bus.Pd.7"],[[5],[6]],["b","c"],name="more")
    ser = case.combined_series()
    assert ser["index"] == ["b"] and ser["values"] == [[2.0,5.0]]

@pytest.mark.parametrize("second,match",[
    ({"columns":["bus.Pd.7"],"values":[[1],[2]]},"have time labels but 'b' do not"),
    ({"columns":["bus.Pd.5"],"values":[[1],[2]],"index":["x","y"]},"'a' and 'b' both give bus.Pd.5"),
    ({"columns":["bus.Pd.7"],"values":[[1],[2]],"index":["x","y"],"delta":0.5},"different step lengths"),
])
def test_combined_errors(second,match):
    """Mixed labels, overlapping columns and different step lengths are refused"""
    case = Case.builtin("case9")
    case.set_series(["bus.Pd.5"],[[1],[2]],["x","y"],name="a")
    case.set_series(name="b",**second)
    with pytest.raises(CaseError,match=match):
        case.combined_series()

def test_several_series_saved():
    """Several series round-trip through .py and .json; the old one-series form still loads"""
    case = Case.builtin("case9")
    case.set_series(["bus.Pd.5"],[[1],[2]],["a","b"],name="load")
    case.set_series(["gen.Pmax.3"],[[100],[90]],name="cap",delta=1.0)
    for fmt in ("py","json"):
        assert Case.load(f"x.{fmt}",case.dumps(fmt)).series == case.series
    data = Case.builtin("case9").to_ppc()
    data["series"] = {"columns":["bus.Pd.5"],"values":[[1]],"index":["t"]}
    old = Case.load("x.json",json.dumps(data))
    assert old.series[0]["name"] == "series" and old.series[0]["timed"]

def test_series_rekey_all():
    """Edits keep every series valid; an emptied series is removed"""
    case = Case.builtin("case9")
    case.set_series(["bus.Pd.5"],[[1]],name="load")
    case.set_series(["bus.Pd.5","gen.Pmax.3"],[[1,2]],name="other")
    case.delete_rows("bus",[4],cascade=True)
    assert [x["name"] for x in case.series] == ["other"]
    assert case.get_series("other")["columns"] == ["gen.Pmax.3"]

@needs_cvxopf
def test_multistep_several_series():
    """Multistep OPF combines a load series and a generator limit series by time"""
    case = Case.builtin("case9")
    case.set_series(["bus.Pd.5"],[[90],[150],[150]],["2026-07-01 00:00","2026-07-01 01:00","2026-07-01 02:00"],name="load")
    case.set_series(["gen.Pmax.3"],[[270],[60]],["2026-07-01 00:00","2026-07-01 01:00"],name="cap")
    r = case.solve("opf","CVXOPF-DC",multistep=True)
    assert r["success"] and r["multistep"]["steps"] == 2 and r["multistep"]["series"] == ["load","cap"]
    assert r["steps"][1]["gen"][2]["Pg"] <= 60 + 1e-3
    assert sum(a["Pd"] for a in r["steps"][1]["areas"]) == pytest.approx(150+100+125)
    case.set_series(["bus.Pd.7"],[[1]],name="bad")
    with pytest.raises(CaseError,match="have time labels but 'bad' do not"):
        case.solve("opf","CVXOPF-DC",multistep=True)

def test_server_several_series(client,post):
    """Imports add series by file name or name; delta, export and removal take a name"""
    post("/api/series/import",{"filename":"load.csv","data":_b64("time,bus.Pd.5\na,90\nb,95\n")})
    status,st = post("/api/series/import",{"filename":"x.csv","data":_b64("time,gen.Pmax.3\na,200\nb,100\n"),"name":"cap"})
    assert [x["name"] for x in st["case"]["series"]] == ["load","cap"]
    assert st["case"]["series_combined"]["steps"] == 2 and st["imported"]["replaced"] is False
    status,st = post("/api/series",{"delta":2})
    assert status == 400 and "name the series" in st["error"]
    status,st = post("/api/series",{"delta":2,"name":"cap"})
    assert _ser(st,"cap")["delta"] == 2 and "different step lengths" in st["case"]["series_combined"]["error"]
    post("/api/series",{"delta":1,"name":"cap"})
    res = client.get("/api/series/export?format=csv")
    assert res.data.decode().startswith("time,bus.Pd.5,gen.Pmax.3")
    assert "_series.csv" in res.headers["Content-Disposition"]
    res = client.get("/api/series/export?format=csv&name=cap")
    assert res.data.decode().startswith("time,gen.Pmax.3") and "_cap.csv" in res.headers["Content-Disposition"]
    status,st = post("/api/series",{"clear":True,"name":"load"})
    assert [x["name"] for x in st["case"]["series"]] == ["cap"]
    status,st = post("/api/series",{"clear":True,"name":"nope"})
    assert status == 400 and "no series named" in st["error"]
