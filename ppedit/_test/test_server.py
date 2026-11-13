"""Tests for `ppedit.server` (fast, no network)"""

def test_index_and_meta(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    meta = client.get("/api/meta").get_json()
    assert "case14" in meta["builtin"] and meta["tables"][0] == "bus"

def test_post_requires_header(client):
    """Cross-origin pages cannot post without the custom header"""
    res = client.post("/api/new",json={})
    assert res.status_code == 403

def test_remote_host_refused(client):
    res = client.get("/api/state",headers={"Host":"evil.example.com"})
    assert res.status_code == 403

def test_edit_undo_redo(post):
    status,st = post("/api/cell",{"table":"bus","row":4,"column":"Pd","value":"95"})
    assert status == 200 and st["case"]["tables"]["bus"][4][2] == 95 and st["dirty"]
    _,st = post("/api/undo")
    assert st["case"]["tables"]["bus"][4][2] == 90 and st["can_redo"]
    _,st = post("/api/redo")
    assert st["case"]["tables"]["bus"][4][2] == 95

def test_bad_edit_rolls_back(post):
    status,st = post("/api/cell",{"table":"bus","row":0,"column":"Pd","value":"x"})
    assert status == 400 and "not a number" in st["error"]
    _,st = post("/api/undo")
    assert "error" in st # nothing was recorded

def test_rows_and_cascade(post):
    _,st = post("/api/rows",{"table":"bus"})
    assert st["row"] == 9 and st["case"]["tables"]["bus"][9][0] == 10
    status,_ = post("/api/rows/delete",{"table":"bus","rows":[0]})
    assert status == 400
    _,st = post("/api/rows/delete",{"table":"bus","rows":[0],"cascade":True})
    assert st["removed"]["gen"] == 1

def test_solve_apply_and_clear(post):
    _,st = post("/api/solve",{"kind":"opf"})
    assert st["result"]["success"]
    _,st = post("/api/apply")
    assert st["result"] is None # edits clear the solution

def test_open_upload_and_export(client,post):
    text = client.get("/api/export?format=glm").get_data(as_text=True)
    _,st = post("/api/open",{"filename":"x.glm","text":text})
    assert len(st["case"]["tables"]["bus"]) == 9 and st["path"] is None
    status,st = post("/api/save",{})
    assert status == 400 # no file yet

def test_save(tmp_path,post):
    path = str(tmp_path/"out.glm")
    _,st = post("/api/save",{"path":path})
    assert st["path"] == path and not st["dirty"]
    _,st = post("/api/open",{"path":path})
    assert len(st["case"]["tables"]["branch"]) == 9

def test_save_guard_for_dropped_content(tmp_path):
    """Overwriting a GLM whose extra content would be dropped needs force"""
    import os
    from ppedit import Case, create_app
    src = os.path.join(os.path.dirname(__file__),"data","case3.glm")
    path = tmp_path/"case3.glm"
    path.write_text(open(src,encoding="utf-8").read())
    app = create_app(Case.load(str(path)),str(path))
    client = app.test_client()
    res = client.post("/api/save",json={},headers={"X-PPEdit":"1"})
    assert res.status_code == 409 and res.get_json()["confirm"]
    assert "powerplant" in path.read_text() # untouched
    res = client.post("/api/save",json={"path":str(tmp_path/"new.glm")},headers={"X-PPEdit":"1"})
    assert res.status_code == 200 # new file is fine
    res = client.post("/api/save",json={"path":str(path),"force":True},headers={"X-PPEdit":"1"})
    assert res.status_code == 200 and "powerplant" not in path.read_text()

def test_dcline_api(client,post):
    """DC lines are edited, solved, applied, undone and exported through the API"""
    meta = client.get("/api/meta").get_json()
    assert "dcline" in meta["tables"] and "dclinecost" in meta["tables"]
    status,st = post("/api/rows",{"table":"dcline","values":{"fbus":2,"tbus":9,"Pf":20}})
    assert status == 200 and st["case"]["tables"]["dcline"][0][:4] == [2,9,1,20.0]
    status,st = post("/api/cell",{"table":"dcline","row":0,"column":"tbus","value":"99"})
    assert status == 400 and "does not exist" in st["error"]
    _,st = post("/api/solve",{"kind":"pf"})
    assert st["result"]["success"] and st["result"]["dcline"][0]["Pt"] == 20.0
    _,st = post("/api/apply")
    assert st["case"]["tables"]["dcline"][0][4] == 20.0
    assert "object pypower.dcline" in client.get("/api/export?format=glm").get_data(as_text=True)
    _,st = post("/api/rows/delete",{"table":"bus","rows":[1],"cascade":True})
    assert st["removed"]["dcline"] == 1 and st["case"]["tables"]["dcline"] == []
    _,st = post("/api/undo")
    assert len(st["case"]["tables"]["dcline"]) == 1
