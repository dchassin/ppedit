"""Tests for geodata in `ppedit.case`, the API, and the CLI (fast, no network)"""

import json
import os

import pytest

from ppedit import Case, CaseError, main

DATA = os.path.join(os.path.dirname(__file__),"data")
CSV = os.path.join(DATA,"case9_geodata.csv")

@pytest.fixture
def case():
    """case9 with the curated geodata (tags NAME, KV)"""
    case = Case.builtin("case9")
    case.load_geodata(CSV)
    return case

def test_csv_columns(case):
    """BUS_I, LAT, LON are required; other columns become tags"""
    assert case.geotags == ["NAME","KV"]
    assert len(case.geodata) == 9
    assert case.geodata[0] == [1,38.5816,-121.4944,"Sacramento",345]
    assert not case.validate()

def test_csv_any_order_and_case():
    case = Case.builtin("case9")
    assert case.load_geodata("g.csv","lon,Bus_i,LAT\n-120,1,37\n") == 1
    assert case.geodata == [[1,37.0,-120.0]] and case.geotags == []

@pytest.mark.parametrize("text,message",[
    ("BUS_I,LAT\n1,37\n",r"missing column\(s\) LON"),
    ("BUS_I,LAT,LON\nx,37,-120\n","not a number"),
    ("BUS_I,LAT,LON\n1,97,-120\n","latitude"),
    ("BUS_I,LAT,LON\n1,37,-190\n","longitude"),
    ("","empty"),
])
def test_csv_errors(text,message):
    with pytest.raises(CaseError,match=message):
        Case.builtin("case9").load_geodata("g.csv",text)

@pytest.mark.parametrize("fmt,ext",[("py",".py"),("json",".json")])
def test_round_trip_case_files(case,fmt,ext):
    """py and json keep geodata (key geodata, an array) and geotags"""
    text = case.dumps(fmt)
    again = Case.load(f"x{ext}",text)
    assert again.geodata == case.geodata and again.geotags == case.geotags
    assert again.tables == case.tables
    if fmt == "json":
        data = json.loads(text)
        assert isinstance(data["geodata"],list) and data["geotags"] == ["NAME","KV"]

def test_glm_keeps_locations_only(case):
    text = case.dumps("glm")
    assert "latitude 38.5816;" in text and "longitude -121.4944;" in text
    again = Case.load("x.glm",text)
    assert again.geodata == [row[:3] for row in case.geodata] and again.geotags == []

def test_glm_dms_location():
    glm = """module pypower;
object pypower.bus { bus_i 1; type REF; latitude 37N30:00; longitude 122W15:00; }
"""
    case = Case.load("x.glm",glm)
    assert case.geodata == [[1,37.5,-122.25]]

def test_geodata_dict_of_columns():
    """A geodata dict of columns in a case file is accepted"""
    data = Case.builtin("case9").to_ppc()
    data["geodata"] = {"BUS_I":[1,2],"LAT":[37,38],"LON":[-120,-121],"OWNER":["A","B"]}
    case = Case(data)
    assert case.geodata == [[1,37.0,-120.0,"A"],[2,38.0,-121.0,"B"]] and case.geotags == ["OWNER"]

def test_no_geodata_stays_out():
    case = Case.builtin("case9")
    assert "geodata" not in case.to_ppc() and "geodata" not in case.dumps("py")
    assert case.to_dict()["geodata"] == []

def test_not_passed_to_solver(case):
    assert "geodata" not in case.to_ppc(arrays=True)
    assert case.solve("pf")["success"]

def test_bus_edits_cascade(case):
    """Renumbering a bus renumbers its geodata; deleting it removes it"""
    assert case.set_value("bus",0,"bus_i",10)
    assert case.geodata[0][0] == 10
    case.delete_rows("bus",[0],cascade=True)
    assert all(row[0] != 10 for row in case.geodata) and len(case.geodata) == 8

def test_validate_warnings(case):
    case.geodata.append([1,0.0,0.0,"dup",0])
    case.geodata.append([99,0.0,0.0,"",""])
    del case.geodata[8] # bus 9 now missing (index 8 is bus 9)
    messages = [x["message"] for x in case.validate()]
    assert all(x["level"] == "warning" for x in case.validate())
    assert any("more than one geodata row" in m for m in messages)
    assert any("bus 99 does not exist" in m for m in messages)
    assert any("1 bus(es) have no geodata: 9" in m for m in messages)

def test_csv_export(case):
    text = case.dumps_geodata()
    assert text.splitlines()[0] == "BUS_I,LAT,LON,NAME,KV"
    again = Case.builtin("case9")
    again.load_geodata("x.csv",text)
    assert again.geodata == case.geodata

def test_api_load_undo_clear(client,post):
    text = open(CSV,encoding="utf-8").read()
    status,st = post("/api/geodata",{"filename":"g.csv","text":text})
    assert status == 200 and len(st["case"]["geodata"]) == 9 and st["case"]["geotags"] == ["NAME","KV"]
    assert client.get("/api/export?format=csv").get_data(as_text=True).startswith("BUS_I,LAT,LON")
    _,st = post("/api/geodata",{"clear":True})
    assert st["case"]["geodata"] == []
    _,st = post("/api/undo")
    assert len(st["case"]["geodata"]) == 9
    status,st = post("/api/geodata",{"filename":"g.csv","text":"BUS_I,LAT\n1,2\n"})
    assert status == 400 and "LON" in st["error"]
    status,st = post("/api/geodata",{"path":CSV})
    assert status == 200

def test_cli_convert_with_geodata(tmp_path):
    out = tmp_path/"case9.json"
    src = tmp_path/"case9.py"
    src.write_text(Case.builtin("case9").dumps("py"))
    assert main(["convert",str(src),str(out),"--geodata",CSV]) == 0
    assert len(json.loads(out.read_text())["geodata"]) == 9
