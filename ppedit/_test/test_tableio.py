"""Tests for table data import and export (issue #73)"""

import base64
import math

import pytest

from ppedit import Case, COLUMNS
from ppedit.case import CaseError
from ppedit._tableio import table_formats, table_format, read_rows, dump_rows

FORMATS = table_formats()
NAMES = {t:[c["name"] for c in COLUMNS[t]] for t in COLUMNS}

def test_formats():
    """Formats come from the extension, compression only for text formats"""
    assert {".csv",".csv.gz",".tsv",".json",".json.bz2"} <= set(FORMATS)
    assert table_format("Bus.CSV") == ("csv",None)
    assert table_format("/tmp/gen.csv.gz") == ("csv","gzip")
    assert table_format("x.json.xz") == ("json","xz")
    for bad in ["bus.txt","bus","bus.gz","bus.pickle"]:
        with pytest.raises(CaseError,match="not a supported"):
            table_format(bad)
    if ".parquet" in FORMATS:
        with pytest.raises(CaseError,match="cannot be compressed"):
            table_format("bus.parquet.gz")

@pytest.mark.parametrize("ext",FORMATS)
@pytest.mark.parametrize("table",["bus","gen","branch","gencost","areas"])
def test_round_trip(table,ext):
    """Export then import with every row selected gives the same table"""
    case = Case.builtin("case9")
    case.tables["gen"][0][3] = math.inf  # Qmax
    case.tables["areas"][0][2] = 'North, "A"'
    columns,rows = read_rows("x"+ext,dump_rows(table,case.tables[table],"x"+ext))
    assert columns == NAMES[table]
    other = Case.builtin("case9")
    out = other.import_rows(table,columns,rows,replace=list(range(len(rows))))
    assert out["replaced"] == len(rows)
    assert other.tables[table] == case.tables[table]

def test_append_defaults():
    """Appending takes any subset of columns; missing values get defaults"""
    case = Case.builtin("case9")
    out = case.import_rows("bus",[" bus_i","Pd","type"],[[10,25,1],[None,5,1]])
    assert out == {"added":2,"cascade":False}
    assert case.tables["bus"][-2][:3] == [10,1,25.0]
    assert case.tables["bus"][-1][0] == 11  # default: next bus number
    out = case.import_rows("gen",["bus","Pg"],[[10,40]])
    assert out["cascade"] and len(case.tables["gen"]) == len(case.tables["gencost"]) == 4

def test_replace_renumbers():
    """Replacing bus rows may exchange bus numbers; references follow"""
    case = Case.builtin("case9")
    rows = [list(r) for r in case.tables["bus"][:2]]
    rows[0][0],rows[1][0] = 2,1
    gens = [g[0] for g in case.tables["gen"]]
    out = case.import_rows("bus",NAMES["bus"],rows,replace=[1,0])
    assert out == {"replaced":2,"cascade":True}
    assert [b[0] for b in case.tables["bus"][:2]] == [2,1]
    assert [g[0] for g in case.tables["gen"]] == [{1:2,2:1}.get(b,b) for b in gens]

def test_import_errors():
    """Bad columns, counts and values are refused with a clear message"""
    case = Case.builtin("case9")
    bus = [list(r) for r in case.tables["bus"]]
    for args,match in [
            ((["foo"],[[1]]),"no column"),
            ((["Pd","Pd"],[[1,2]]),"more than once"),
            ((["Pd"],[]),"no rows"),
            ((["bus_i"],[[1]]),"row 1: bus 1 already exists"),
            ((["bus_i"],[[1]],[0]),"needs every column"),
            ((NAMES["bus"],bus[:2],[0]),"2 row.* for 1 selected"),
            ((NAMES["bus"],bus[:1],[99]),"does not exist"),
            ((NAMES["bus"],[bus[0][:2]+[None]+bus[0][3:]],[0]),"row 1: no value for Pd"),
            ((NAMES["bus"],[bus[0][:1]+["x"]+bus[0][2:]],[0]),"row 1: 'x' is not a number"),
            ]:
        with pytest.raises(CaseError,match=match):
            case.import_rows("bus",*args)

def test_read_bad_file():
    with pytest.raises(CaseError,match="cannot read"):
        read_rows("x.json",b"not json")

def _upload(post,table,name,data,rows=None):
    body = {"table":table,"filename":name,"data":base64.b64encode(data).decode()}
    if rows is not None:
        body["rows"] = rows
    return post("/api/table/import",body)

def test_server_export_import(client,post):
    """Export all or selected rows; import appends or replaces as one undo step"""
    assert client.get("/api/meta").get_json()["table_formats"] == FORMATS
    res = client.get("/api/table/export?table=bus&format=csv")
    assert res.status_code == 200
    assert 'filename="case9_bus.csv"' in res.headers["Content-Disposition"]
    data = res.get_data()
    assert data.decode().splitlines()[0] == ",".join(NAMES["bus"])
    assert len(data.decode().splitlines()) == 10
    part = client.get("/api/table/export?table=bus&format=csv.gz&rows=4,2").get_data()
    columns,rows = read_rows("x.csv.gz",part)
    state = client.get("/api/state").get_json()
    assert rows == [state["case"]["tables"]["bus"][i] for i in (2,4)]

    rows = [list(r) for r in rows]
    rows[0][2] = 77.0
    status,state = _upload(post,"bus","b.csv",dump_rows("bus",rows,"b.csv"),[2,4])
    assert status == 200 and state["imported"]["replaced"] == 2
    assert state["case"]["tables"]["bus"][2][2] == 77.0 and state["can_undo"]
    status,state = post("/api/undo")
    assert state["case"]["tables"]["bus"][2][2] != 77.0 and not state["can_undo"]

    new = dump_rows("bus",[[20]+rows[0][1:],[21]+rows[1][1:]],"b.parquet") \
        if ".parquet" in FORMATS else b"bus_i,type\n20,1\n21,1\n"
    status,state = _upload(post,"bus","b.parquet" if ".parquet" in FORMATS else "b.csv",new)
    assert status == 200 and state["imported"] == {"added":2,"cascade":False}
    assert [b[0] for b in state["case"]["tables"]["bus"][-2:]] == [20,21]

@pytest.mark.parametrize("ext",FORMATS)
def test_server_export_extended(client,post,ext):
    """Export adds the solution's extended columns the page shows; import
    ignores them (issue #108)"""
    fmt = ext.lstrip(".")
    status,state = post("/api/solve",{"kind":"pf"})
    assert status == 200 and state["result"]["success"]
    res = client.get(f"/api/table/export?table=branch&format={fmt}&rows=2,0")
    assert res.status_code == 200
    columns,rows = read_rows(f"x{ext}",res.get_data())
    assert columns == NAMES["branch"] + ["PF","QF","PT","QT"] # PF: no multipliers
    ext_rows = state["result"]["extended"]["branch"]
    for row,i in zip(rows,(0,2)):
        assert row[:len(NAMES["branch"])] == state["case"]["tables"]["branch"][i]
        assert row[len(NAMES["branch"]):] == pytest.approx(ext_rows[i][:4])
    bus = read_rows(f"x{ext}",client.get(f"/api/table/export?table=bus&format={fmt}").get_data())
    assert bus[0] == NAMES["bus"] # no bus values from PF
    status,state = _upload(post,"branch",f"b{ext}",res.get_data(),[0,2]) # solution columns ignored
    assert status == 200 and state["imported"]["ignored"] == ["PF","QF","PT","QT"]
    status,state = post("/api/cell",{"table":"branch","row":0,"column":"rateA","value":200})
    assert status == 200 and state["result"] is None # an edit clears the solution
    columns,_ = read_rows(f"x{ext}",
        client.get(f"/api/table/export?table=branch&format={fmt}").get_data())
    assert columns == NAMES["branch"]

def test_server_import_rolls_back(client,post):
    """A failing row leaves the case unchanged"""
    before = client.get("/api/state").get_json()["case"]["tables"]["bus"]
    status,state = _upload(post,"bus","b.csv",b"bus_i,type\n30,1\n1,1\n")
    assert status == 400 and "row 2" in state["error"]
    after = client.get("/api/state").get_json()
    assert after["case"]["tables"]["bus"] == before and not after["can_undo"]
    assert _upload(post,"bus","b.csv",b"x")[0] == 400
    assert post("/api/table/import",{"table":"bus","filename":"b.csv","data":"%%"})[0] == 400

def test_server_export_errors(client):
    assert client.get("/api/table/export?table=nope&format=csv").status_code == 400
    assert client.get("/api/table/export?table=bus&format=pickle").status_code == 400
    assert client.get("/api/table/export?table=bus&format=csv&rows=99").status_code == 400
    assert client.get("/api/table/export?table=bus&format=csv&rows=a").status_code == 400

def test_page_has_table_io(client):
    page = client.get("/").get_data(as_text=True)
    assert 'data-action="import-rows"' in page and 'id="export-menu"' in page
    assert "/api/table/export" in client.get("/static/app.js").get_data(as_text=True)

def test_replace_costs_ncost():
    """Imported cost rows set ncost from model and costs"""
    case = Case.builtin("case9")
    row = list(case.tables["gencost"][0])
    row[3],row[4] = 7,"0.1,2,3,4"
    case.import_rows("gencost",NAMES["gencost"],[row],replace=[0])
    assert case.tables["gencost"][0][3:] == [4,"0.1,2,3,4"]
