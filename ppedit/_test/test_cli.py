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

def test_version(capsys):
    import pytest # pylint: disable=import-outside-toplevel
    from ppedit import __version__ # pylint: disable=import-outside-toplevel
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"ppedit {__version__}"

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
    printed = capsys.readouterr().out
    assert f"URL.... http://localhost:{port}/" in printed
    assert "thanks for using ppedit!" in printed

def test_verbose(tmp_path,caplog):
    import logging # pylint: disable=import-outside-toplevel
    out = str(tmp_path/"case3.json")
    with caplog.at_level(logging.NOTSET):
        assert main(["convert",os.path.join(DATA,"case3.glm"),out]) == 0
        assert not [r for r in caplog.records if r.levelno == logging.INFO]
        assert main(["convert","-v",os.path.join(DATA,"case3.glm"),out]) == 0
        messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any(m.startswith("loaded ") for m in messages)
    assert any(m.startswith("saved ") for m in messages)
    assert logging.getLogger("werkzeug").level == logging.INFO
    for argv in (["check"],["check","-d"]):
        main(argv+[out])
        assert logging.getLogger("werkzeug").level == logging.WARNING
