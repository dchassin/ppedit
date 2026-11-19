"""Tests for `ppedit.server` (fast, no network)"""

import importlib.util

def test_index_and_meta(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    meta = client.get("/api/meta").get_json()
    assert "case14" in meta["builtin"] and meta["tables"][0] == "bus"
    assert meta["solvers"]["pf"][0] == {"algorithm":"NR","label":"Newton"}
    assert meta["solvers"]["opf"][0]["algorithm"] == "PIPS"
    assert "cvxopf" in meta["solvers"]

def test_static_files_not_cached(client):
    """The page and its files are revalidated on every load"""
    for url in ["/","/static/app.js","/static/style.css"]:
        assert client.get(url).headers["Cache-Control"] == "no-cache"

def test_index_has_network_search(client):
    """The network search box follows the Map menu and app.js handles it"""
    page = client.get("/").get_data(as_text=True)
    assert 'id="net-search"' in page and 'id="search-menu"' in page
    assert page.index('data-menu="map-menu"') < page.index('id="net-search"')
    js = client.get("/static/app.js").get_data(as_text=True)
    assert "function searchItems(" in js and "setupSearch();" in js

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

def test_solve_algorithm(post):
    """The menu's algorithm reaches Case.solve; a wrong one is a 400 with the message"""
    _,st = post("/api/solve",{"kind":"opf","algorithm":"DC-PIPS"})
    assert st["result"]["success"] and st["result"]["dc"]
    code,st = post("/api/solve",{"kind":"pf","algorithm":"PIPS"})
    assert code == 400 and "PF algorithm" in st["error"]

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

def test_exit():
    import threading # pylint: disable=import-outside-toplevel
    from ppedit import Case,create_app # pylint: disable=import-outside-toplevel
    stopped = threading.Event()
    app = create_app(Case.builtin("case9"),shutdown=stopped.set)
    res = app.test_client().post("/api/exit",json={},headers={"X-PPEdit":"1"})
    assert res.status_code == 200 and res.get_json() == {"exit":True}
    assert stopped.wait(2)
    res = app.test_client().post("/api/exit",json={})
    assert res.status_code == 403

def test_areas_api(client,post):
    """Areas are edited, solved, cascaded and undone through the API"""
    meta = client.get("/api/meta").get_json()
    assert "areas" in meta["tables"]
    st = client.get("/api/state").get_json()
    assert st["case"]["tables"]["areas"] == [[1,5,""]]
    status,st = post("/api/cell",{"table":"bus","row":8,"column":"area","value":"2"})
    assert status == 200
    assert any("area 2" in x["message"] for x in st["issues"])
    status,st = post("/api/rows",{"table":"areas"})
    assert status == 200 and st["case"]["tables"]["areas"][1] == [2,9,""]
    _,st = post("/api/solve",{"kind":"pf"})
    assert [a["area"] for a in st["result"]["areas"]] == [1,2]
    status,st = post("/api/rows/delete",{"table":"bus","rows":[8]})
    assert status == 400 and "price reference bus" in st["error"]
    _,st = post("/api/rows/delete",{"table":"bus","rows":[8],"cascade":True})
    assert st["removed"]["areas"] == 1 and st["case"]["tables"]["areas"] == [[1,5,""]]
    _,st = post("/api/undo")
    assert st["case"]["tables"]["areas"] == [[1,5,""],[2,9,""]]

class _Response:
    """Stand-in for `urllib.request.urlopen` results"""
    def __init__(self,data,ctype="text/plain; charset=utf-8"):
        self.data = data
        self.headers = {"Content-Type":ctype}
    def read(self,size=-1):
        return self.data if size < 0 else self.data[:size]
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False

def test_open_url(client,post,monkeypatch):
    """A case opened from a URL is downloaded by the server (no network here)"""
    import urllib.error
    from ppedit import server
    text = client.get("/api/export?format=glm").get_data(as_text=True)
    seen = []
    def fake_urlopen(req,timeout=None):
        seen.append(req.full_url)
        if req.full_url.endswith("missing.glm"):
            raise urllib.error.HTTPError(req.full_url,404,"Not Found",{},None)
        if req.full_url.endswith("down.glm"):
            raise urllib.error.URLError("no route to host")
        if req.full_url.endswith("page.glm"):
            return _Response(b"<html></html>","text/html; charset=utf-8")
        return _Response(text.encode())
    monkeypatch.setattr(server.urllib.request,"urlopen",fake_urlopen)
    url = "https://example.org/cases/my%20case.glm?raw=1"
    _,st = post("/api/open",{"url":url})
    assert seen == [url]
    assert len(st["case"]["tables"]["bus"]) == 9 and st["path"] is None
    assert st["source"]["file"] == url and st["source"]["format"] == "glm"
    assert st["case"]["name"] == "my_case"
    status,st = post("/api/save",{})
    assert status == 400 # no file yet: Save as
    for url,msg in [("file:///etc/passwd.glm","only http and https"),
                    ("ftp://example.org/x.glm","only http and https"),
                    ("https://example.org/case.txt","does not name"),
                    ("https://example.org/missing.glm","HTTP 404"),
                    ("https://example.org/down.glm","cannot download"),
                    ("https://example.org/page.glm","is a web page")]:
        status,st = post("/api/open",{"url":url})
        assert status == 400 and msg in st["error"], url
    seen.clear() # GitHub file pages are fetched from raw.githubusercontent.com
    _,st = post("/api/open",{"url":"https://github.com/o/r/blob/feature/x/case9.glm"})
    assert seen == ["https://raw.githubusercontent.com/o/r/feature/x/case9.glm"]
    assert st["source"]["file"] == seen[0]
    monkeypatch.setattr(server,"DOWNLOAD_LIMIT",10)
    status,st = post("/api/open",{"url":"https://example.org/big.glm"})
    assert status == 400 and "larger than" in st["error"]

def test_solve_messages_and_options(post):
    """Solver messages come back in the result; cvxopf options reach Case.solve"""
    _,st = post("/api/rows",{"table":"bus","values":{"Pd":10}})
    _,st = post("/api/solve",{"kind":"pf"})
    assert st["result"]["messages"][0]["level"] == "warning"
    code,st = post("/api/solve",{"kind":"opf","algorithm":"PIPS","options":{"verbose":True}})
    assert code == 200 and st["result"]["options"] is None # ignored by other solvers
    if importlib.util.find_spec("cvxopf") is not None:
        code,st = post("/api/solve",{"kind":"opf","algorithm":"CVXOPF-DC",
            "options":{"preparation":"recommended"}})
        assert code == 200 and st["result"]["options"]["preparation"] == "recommended"
        code,st = post("/api/solve",{"kind":"opf","algorithm":"CVXOPF-DC",
            "options":{"preparation":"nope"}})
        assert code == 400 and "preparation" in st["error"]

def test_folders(tmp_path,post):
    """The folder chooser lists subfolders and savable case files"""
    (tmp_path/"sub").mkdir()
    (tmp_path/".hidden").mkdir()
    (tmp_path/"a.glm").write_text("")
    (tmp_path/"notes.txt").write_text("")
    status,ls = post("/api/folders",{"path":str(tmp_path)})
    assert status == 200 and ls["path"] == str(tmp_path)
    assert ls["folders"] == ["sub"] and ls["files"] == ["a.glm"]
    assert ls["parent"] == str(tmp_path.parent)
    path = str(tmp_path/"sub"/"out.json")
    _,st = post("/api/save",{"path":path})
    _,ls = post("/api/folders",{}) # default: the open file's folder
    assert ls["path"] == str(tmp_path/"sub") and ls["files"] == ["out.json"]
    status,ls = post("/api/folders",{"path":str(tmp_path/"missing")})
    assert status == 400 and "not a folder" in ls["error"]

def test_folder_moves_path_without_saving(tmp_path,post):
    """Choosing a folder changes where Save writes, but writes nothing"""
    _,st = post("/api/new",{"builtin":"case9"})
    assert st["path"] is None
    _,st = post("/api/folder",{"path":str(tmp_path)})
    assert st["path"] == str(tmp_path/"case9.glm") and st["dirty"]
    assert not list(tmp_path.iterdir())
    (tmp_path/"sub").mkdir()
    _,st = post("/api/folder",{"path":str(tmp_path/"sub")})
    assert st["path"] == str(tmp_path/"sub"/"case9.glm") # file name kept
    _,st = post("/api/save")
    assert (tmp_path/"sub"/"case9.glm").exists() and not st["dirty"]
    _,st = post("/api/folder",{"path":str(tmp_path/"sub")}) # same folder
    assert not st["dirty"]
    status,st = post("/api/folder",{"path":str(tmp_path/"missing")})
    assert status == 400 and "not a folder" in st["error"]

def test_index_has_folder_chooser(client):
    """The top bar path opens the folder chooser"""
    page = client.get("/").get_data(as_text=True)
    assert 'id="case-path"' in page and 'id="folder-menu"' in page
    js = client.get("/static/app.js").get_data(as_text=True)
    assert "function toggleFolderMenu(" in js and "/api/folders" in js
