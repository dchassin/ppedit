"""Tests for `ppedit.claude` and the `/api/claude` routes (fast, no network)

A fake API client replays scripted responses, so no API key is needed.
"""

import copy

import pytest

from ppedit import Case, create_app

def _client(*responses):
    """Fake Messages API: returns `responses` in turn and records requests"""
    calls = []
    def client(payload):
        calls.append(copy.deepcopy(payload))
        return responses[min(len(calls),len(responses))-1]
    client.calls = calls
    return client

def _tool(name,args,tid="t1"):
    return {"content":[{"type":"tool_use","id":tid,"name":name,"input":args}],
        "stop_reason":"tool_use","usage":{"input_tokens":10,"output_tokens":5}}

def _text(text):
    return {"content":[{"type":"text","text":text}],"stop_reason":"end_turn",
        "usage":{"input_tokens":3,"output_tokens":2}}

def _app(client):
    app = create_app(Case.builtin("case9"),claude_client=client)
    app.config["TESTING"] = True
    test = app.test_client()
    def post(path,body=None):
        res = test.post(path,json=body or {},headers={"X-PPEdit":"1"})
        return res.status_code,res.get_json()
    return app,test,post

def _wait(app,sid):
    app.config["CLAUDE"].get(sid).wait(10)

def test_session_edits_case_and_undo():
    client = _client(_tool("set_cells",{"edits":[{"table":"bus","row":4,"column":"Pd","value":95}]}),
        _text("Raised the load at bus 5 to 95 MW."))
    app,test,post = _app(client)
    status,st = post("/api/claude/new",{"title":"loads"})
    assert status == 200 and st["claude"]["sessions"][0]["title"] == "loads"
    rev = st["revision"]
    _,st = post("/api/claude/send",{"id":1,"text":"raise bus 5 load to 95 MW"})
    _wait(app,1)
    data = test.get("/api/claude").get_json()["claude"]
    s = data["sessions"][0]
    assert s["status"] == "idle" and s["edits"] == 1 and data["revision"] == rev + 1
    assert [t["kind"] for t in s["transcript"]] == ["user","tool","assistant"]
    state = test.get("/api/state").get_json()
    assert state["case"]["tables"]["bus"][4][2] == 95 and state["can_undo"]
    _,state = post("/api/undo")
    assert state["case"]["tables"]["bus"][4][2] == 90
    # the tool result went back to the API with the system prompt and tools
    last = client.calls[-1]
    assert last["messages"][-1]["content"][0]["type"] == "tool_result"
    assert any(t["name"] == "get_table" for t in last["tools"])
    assert not any(t["name"] in ("fetch_url","read_file") for t in last["tools"])

def test_tool_error_is_reported_and_rolled_back():
    client = _client(_tool("set_cells",{"edits":[{"table":"bus","row":4,"column":"Pd","value":95},
        {"table":"bus","row":0,"column":"Pd","value":"x"}]}),_text("That failed."))
    app,test,post = _app(client)
    post("/api/claude/new")
    post("/api/claude/send",{"id":1,"text":"go"})
    _wait(app,1)
    result = client.calls[-1]["messages"][-1]["content"][0]
    assert result["is_error"] and "not a number" in result["content"]
    state = test.get("/api/state").get_json()
    assert state["case"]["tables"]["bus"][4][2] == 90 and not state["can_undo"]

def test_local_files_stay_in_root(tmp_path):
    (tmp_path/"loads.csv").write_text("bus,Pd\n5,95\n")
    app,_,post = _app(_client(_text("ok")))
    status,_ = post("/api/claude/new",{"local":True,"root":str(tmp_path)})
    assert status == 200
    session = app.config["CLAUDE"].get(1)
    assert session.run_tool("list_files",{})[0] == ["loads.csv"]
    assert "5,95" in session.run_tool("read_file",{"path":"loads.csv"})[0]["text"]
    with pytest.raises(ValueError,match="outside"):
        session.run_tool("read_file",{"path":"../x"})
    with pytest.raises(ValueError,match="not available"):
        session.run_tool("fetch_url",{"url":"https://example.com"})

def test_case_tools():
    app,_,post = _app(_client(_text("ok")))
    post("/api/claude/new",{"web":True})
    session = app.config["CLAUDE"].get(1)
    assert session.run_tool("get_case",{})[0]["rows"]["bus"] == 9
    rows = session.run_tool("get_table",{"table":"gen","start":1,"count":1})[0]["rows"]
    assert rows == [{"row":1,**{k:v for k,v in rows[0].items() if k != "row"}}] and rows[0]["bus"] == 2
    out,changed = session.run_tool("add_rows",{"table":"bus","rows":[{"bus_i":20,"Pd":5}]})
    assert changed and out["rows"] == [9]
    assert session.run_tool("solve",{"kind":"pf"})[0]["success"]
    assert app.config["EDITOR"].result["success"]
    assert any(t.get("name") == "web_search" for t in session._tools()) # pylint: disable=protected-access

def test_two_sessions_and_delete():
    app,test,post = _app(_client(_text("hello")))
    post("/api/claude/new",{"title":"a"})
    _,st = post("/api/claude/new",{"title":"b","model":"claude-haiku-5-5"})
    assert [s["title"] for s in st["claude"]["sessions"]] == ["a","b"]
    post("/api/claude/send",{"id":2,"text":"hi"})
    _wait(app,2)
    status,st = post("/api/claude/send",{"id":2,"text":" "})
    assert status == 400
    _,st = post("/api/claude/delete",{"id":1})
    assert [s["id"] for s in st["claude"]["sessions"]] == [2]
    assert test.get("/api/claude").get_json()["claude"]["sessions"][0]["transcript"][-1]["text"] == "hello"
    status,_ = post("/api/claude/send",{"id":1,"text":"hi"})
    assert status == 400

def test_no_api_key(monkeypatch,tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY",raising=False)
    monkeypatch.setenv("HOME",str(tmp_path))
    app = create_app(Case.builtin("case9"))
    test = app.test_client()
    assert not test.get("/api/claude").get_json()["claude"]["available"]
    res = test.post("/api/claude/new",json={},headers={"X-PPEdit":"1"})
    assert res.status_code == 400 and "API key" in res.get_json()["error"]
