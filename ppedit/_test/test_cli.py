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
