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

def test_meta_result_columns(client):
    """`/api/meta` lists the extended (result) columns of each table (issue #108)"""
    cols = client.get("/api/meta").get_json()["result_columns"]
    assert set(cols) == {"bus","gen","branch","dcline"}
    assert [c["name"] for c in cols["branch"][:4]] == ["PF","QF","PT","QT"]

def test_meta_about(client):
    """`/api/meta` carries the package metadata the "ppedit" title shows"""
    about = client.get("/api/meta").get_json()["about"]
    assert about["name"] == "ppedit" and about["version"]
    assert about["copyright"] and about["copyright"][0].startswith("Copyright")
    assert about["license"] == "MIT License"
    assert about["repository"].startswith("https://github.com/") and not about["repository"].endswith(".git")
    assert about["issues"].endswith("/issues") and about["license_url"].endswith("/LICENSE")
    assert about["documentation"].startswith("https://")
    assert "funding" in about

def test_readme_funding():
    """The funding section is the README section whose heading mentions it"""
    from ppedit.server import _readme_funding # pylint: disable=import-outside-toplevel
    text = "# Usage\nrun\n# Acknowledgments\nFunded by X.\n\n## Detail\nmore\n# Next\nother\n"
    assert _readme_funding(text) == "Funded by X.\n\n## Detail\nmore"
    assert _readme_funding("# Usage\nrun\n") is None

def test_index_has_about_dialog(client):
    """The "ppedit" title is a button that opens the About dialog"""
    page = client.get("/").get_data(as_text=True)
    assert 'id="brand"' in page and 'id="about"' in page
    assert "showAbout" in client.get("/static/app.js").get_data(as_text=True)

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

def test_ping():
    """`/api/ping` answers with the instance token of `/api/meta`, new for each server (issue #51)"""
    import threading # pylint: disable=import-outside-toplevel
    from ppedit import Case,create_app # pylint: disable=import-outside-toplevel
    app = create_app(Case.builtin("case9"))
    client = app.test_client()
    meta = client.get("/api/meta").get_json()
    ping = client.get("/api/ping").get_json()
    assert ping == {"instance":meta["instance"],"revision":0}
    held,done = threading.Event(),threading.Event()
    def hold(): # another thread holds the lock, as a long solve does (it is an RLock)
        with app.config["EDITOR"].lock:
            held.set()
            done.wait(5)
    worker = threading.Thread(target=hold)
    worker.start()
    try:
        assert held.wait(2)
        assert client.get("/api/ping").get_json()["instance"] == meta["instance"]
    finally:
        done.set()
        worker.join()
    other = create_app(Case.builtin("case9")).test_client().get("/api/ping").get_json()
    assert other["instance"] != meta["instance"]

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

def test_toggle_load(post):
    """A load switched off keeps its values to restore; undo and edits keep that consistent (issue #99)"""
    status,st = post("/api/load",{"bus":5})
    assert status == 200 and st["case"]["tables"]["bus"][4][2:4] == [0,0]
    assert st["loads_off"] == [[5,90,30]] and st["dirty"]
    _,st = post("/api/load",{"bus":5})
    assert st["case"]["tables"]["bus"][4][2:4] == [90,30] and st["loads_off"] == []
    _,st = post("/api/undo")
    assert st["case"]["tables"]["bus"][4][2:4] == [0,0] and st["loads_off"] == [[5,90,30]]
    _,st = post("/api/undo")
    assert st["case"]["tables"]["bus"][4][2:4] == [90,30] and st["loads_off"] == []
    _,st = post("/api/redo")
    assert st["loads_off"] == [[5,90,30]]
    _,st = post("/api/cell",{"table":"bus","row":4,"column":"Pd","value":"10"})
    assert st["loads_off"] == [] # an edit of the load forgets the switched-off values
    status,st = post("/api/load",{"bus":1}) # no load and none switched off
    assert status == 400 and "no switched-off load" in st["error"]
    status,st = post("/api/load",{"bus":99})
    assert status == 400 and "not found" in st["error"]

def test_open_forgets_loads_off(post):
    post("/api/load",{"bus":5})
    _,st = post("/api/new",{"builtin":"case9"})
    assert st["loads_off"] == []

def test_oneline_switches_in_page(client):
    """One-line breakers and switches toggle status; generators open their own panel (issue #99)"""
    js = client.get("/static/app.js").get_data(as_text=True)
    assert 'setStatus("branch", i, on)' in js and 'setStatus("gen", i, on)' in js
    assert 'act("/api/load", { bus: b[0] })' in js and '"gen", i);' in js
    assert 'kind === "gen"' in js


def test_area_inspector_js(client):
    """Area names open the areas row in the inspector with area totals (issue #112)"""
    js = client.get("/static/app.js").get_data(as_text=True)
    for name in ["function areaNameClick","function pickArea","function areaTotals",'kind === "areas"']:
        assert name in js
    css = client.get("/static/style.css").get_data(as_text=True)
    assert "text.area-name { pointer-events: auto; }" in css
