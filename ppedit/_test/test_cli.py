"""Tests for `ppedit.cli` (fast, no network)"""

import os

from ppedit import main

DATA = os.path.join(os.path.dirname(__file__),"data")

def test_convert_and_check(tmp_path,capsys):
    out = str(tmp_path/"case3.py")
    assert main(["convert",os.path.join(DATA,"case3.glm"),out]) == 0
    assert "not kept" in capsys.readouterr().err
    assert main(["check",out]) == 0
    assert "no issues found" in capsys.readouterr().out

def test_check_errors(tmp_path,capsys):
    path = tmp_path/"bad.json"
    path.write_text('{"baseMVA":100,"bus":[[1,1,0,0,0,0,1,1,0,230,1,1.1,0.9]],"gen":[],"branch":[]}')
    assert main(["check",str(path)]) == 1
    assert "no reference" in capsys.readouterr().out

def test_bad_input(capsys):
    assert main(["convert","missing.py"]) == 1
    assert "ERROR [ppedit]" in capsys.readouterr().err

def test_serve_exit(capsys):
    import json,socket,threading,time,urllib.request # pylint: disable=import-outside-toplevel
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        port = sock.getsockname()[1]
    out = {}
    thread = threading.Thread(target=lambda: out.update(code=main(["serve","--no-browser","-p",str(port)])))
    thread.start()
    req = urllib.request.Request(f"http://localhost:{port}/api/exit",data=b"{}",method="POST",
        headers={"Content-Type":"application/json","X-PPEdit":"1"})
    for _ in range(50):
        try:
            with urllib.request.urlopen(req,timeout=2) as res:
                assert json.load(res) == {"exit":True}
            break
        except OSError:
            time.sleep(0.1)
    thread.join(5)
    assert not thread.is_alive() and out["code"] == 0
    assert "server stopped" in capsys.readouterr().out
