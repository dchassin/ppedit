"""Tests for DC lines (`dcline` and `dclinecost` tables; fast, no network)"""

import os

import pytest

from ppedit import Case, CaseError
from ppedit.case import COLUMNS

def _pypower_case(name):
    import pypower # pylint: disable=import-outside-toplevel
    path = os.path.join(os.path.dirname(pypower.__file__),"t",f"{name}.py")
    if not os.path.exists(path):
        pytest.skip(f"pypower test case {name} is not installed")
    return Case.load(path)

@pytest.fixture(name="case")
def fixture_case():
    """PYPOWER's 9-bus test case with 4 DC lines (one out of service) and DC line costs"""
    return _pypower_case("t_case9_dcline")

def test_load(case):
    """dcline rows keep the 17 input columns; dclinecost rows read like gencost"""
    assert case.size() == {"bus":9,"gen":3,"branch":9,"gencost":3,"dcline":4,"dclinecost":4}
    assert all(len(r) == len(COLUMNS["dcline"]) == 17 for r in case.tables["dcline"])
    assert case.tables["dcline"][0] == [30,4,1,10.0,8.9,0.0,0.0,1.01,1.0,1.0,10.0,
        -10.0,10.0,-10.0,10.0,1.0,0.01]
    assert case.tables["dclinecost"][3] == [2,0.0,0.0,2,"7.3,0"]
    assert case.validate() == []

@pytest.mark.parametrize("fmt,ext",[("py",".py"),("json",".json")])
def test_round_trip(case,fmt,ext):
    """dcline and dclinecost survive .py and .json"""
    again = Case.load(f"x{ext}",case.dumps(fmt))
    assert again.tables == case.tables

def test_round_trip_glm(case):
    """dcline survives .glm; dclinecost is not written (no GLM class for it)"""
    text = case.dumps("glm")
    assert text.count("object pypower.dcline") == 4
    assert 'from "pp_bus_30";' in text and "status OUT;" in text
    assert "Sfrom 10+8.9j MVA;" in text # Pf + j Pt, as GridLAB-D passes it to PYPOWER
    assert "4 dclinecost row(s) not written" in text
    again = Case.load("x.glm",text)
    assert again.tables["dcline"] == case.tables["dcline"]
    assert again.tables["dclinecost"] == []
    assert again.notes == []

def test_glm_py2glm_style():
    """GLM written by `gridlabd convert -t pypower`: numeric status, unquoted names, outputs"""
    text = """module pypower { version 2; baseMVA 100; }
object pypower.bus { name pp_bus_1; bus_i 1; type REF; }
object pypower.bus { name pp_bus_2; bus_i 2; type PV; }
object pypower.dcline
{
    name pp_dcline_0;
    from pp_bus_1;
    to pp_bus_2;
    status 1.0;
    Sfrom 10+9.5j MVA;
    Sto -1.5-2j MVA;
    Vfrom 1.01 pu.V;
    Pmax 20 MW;
    loss1 0.05 MW/MW;
    mu_Pmin 0 $/MW;
}
object pypower.dcline { from pp_bus_2; to pp_bus_1; status OUT; Sfrom 10+0d MVA; }
"""
    case = Case.load("x.glm",text)
    assert case.tables["dcline"] == [
        [1,2,1,10.0,9.5,-1.5,-2.0,1.01,1.0,0.0,20.0,0.0,0.0,0.0,0.0,0.0,0.05],
        [2,1,0,10.0,0.0,0.0,0.0,1.0,1.0,0.0,100.0,0.0,0.0,0.0,0.0,0.0,0.0],
    ]
    assert case.notes == []
    with pytest.raises(CaseError,match="dcline object 1: to 'nowhere' is not a bus"):
        Case.load("x.glm",text.replace("to pp_bus_2;","to nowhere;"))

def test_opf_matches_pypower(case):
    """OPF flows match the expected values of PYPOWER's t_dcline test"""
    case.tables["dclinecost"] = []
    result = case.solve("opf")
    assert result["success"]
    expected = [ # Pf, Pt, Qf, Qt, Vf, Vt from pypower/t/t_dcline.py
        [10,     8.9,  -10,       10, 1.0674, 1.0935],
        [2.2776, 2.2776, 0,        0, 1.0818, 1.0665],
        [0,      0,      0,        0, 1.0000, 1.0000],
        [10,     9.5,    0.0563, -10, 1.0778, 1.0665],
    ]
    for got,want in zip(result["dcline"],expected):
        assert [got[k] for k in ("Pf","Pt","Qf","Qt","Vf","Vt")] == pytest.approx(want,abs=2e-3)
    assert [d["solved"] for d in result["dcline"]] == [True,True,False,True]
    assert result["dcline"][3]["mu_Pmax"] == pytest.approx(7.2209,abs=1e-2)

def test_opf_with_cost(case):
    """dclinecost changes the dispatch as in PYPOWER's t_dcline 'poly cost' test"""
    result = case.solve("opf")
    assert result["success"]
    assert result["dcline"][1]["Pf"] == pytest.approx(7.8429,abs=1e-2)
    assert result["dcline"][3]["Pf"] == pytest.approx(6.0549,abs=1e-2)
    assert len(result["gen"]) == 3 # dummy generators are removed

def test_pf(case):
    """Power flow keeps Pf, applies the loss function, and holds the terminal voltages"""
    result = case.solve("pf")
    assert result["success"]
    d = result["dcline"][0]
    assert (d["Pf"],d["Pt"]) == pytest.approx((10,10-(1+0.01*10)))
    assert d["loss_MW"] == pytest.approx(1.1)
    vm = {b["bus_i"]:b["Vm"] for b in result["bus"]}
    assert vm[30] == pytest.approx(1.01) and vm[9] == pytest.approx(0.98)
    assert result["dcline_losses_MW"] == pytest.approx(1.1+0+0.5)
    assert "DC lines" in result["log"]
    assert result["losses_MW"] > result["dcline_losses_MW"]

def test_apply_result(case):
    """Applying a solution copies in-service DC line flows"""
    result = case.solve("opf")
    case.apply_result(result)
    assert case.tables["dcline"][1][3] == pytest.approx(result["dcline"][1]["Pf"])
    assert case.tables["dcline"][2][3:7] == [0.0,0.0,0.0,0.0] # out of service: unchanged

def test_isolated_terminal():
    """A DC line to a bus with no in-service branch is not solved"""
    case = Case.builtin("case9")
    case.add_row("bus",{"bus_i":10,"type":1,"Pd":5})
    case.add_row("dcline",{"fbus":1,"tbus":10,"Pf":5})
    result = case.solve("pf")
    assert result["success"]
    assert not result["dcline"][0]["solved"]
    assert "DC line(s) 1 not solved" in result["log"]

def test_add_to_builtin():
    """A DC line added to case9 solves and moves power between its buses"""
    case = Case.builtin("case9")
    row = case.add_row("dcline",{"fbus":2,"tbus":9,"Pf":20,"loss1":0.02,
        "QminF":-50,"QmaxF":50,"QminT":-50,"QmaxT":50})
    assert case.tables["dcline"][row][:3] == [2,9,1]
    result = case.solve("pf")
    assert result["success"]
    assert result["dcline"][0]["Pt"] == pytest.approx(19.6)
    # generation = load + AC and DC losses (case9 has no shunts)
    load = sum(b[2] for b in case.tables["bus"])
    assert sum(g["Pg"] for g in result["gen"]) == pytest.approx(load+result["losses_MW"])
    assert result["dcline_losses_MW"] == pytest.approx(0.4)
    opf = case.solve("opf")
    assert opf["success"]
    assert 0 <= opf["dcline"][0]["Pf"] <= 100

def test_validate(case):
    """DC line errors and warnings"""
    def messages(edit):
        c = _pypower_case("t_case9_dcline")
        edit(c)
        return [(i["table"],i["column"],i["message"]) for i in c.validate()]
    assert ("dcline","Pmin","Pmin exceeds Pmax") in messages(
        lambda c: c.tables["dcline"][0].__setitem__(9,20))
    assert ("dcline","tbus","DC line connects a bus to itself") in messages(
        lambda c: c.tables["dcline"][0].__setitem__(1,30))
    assert ("dcline","loss1","loss1 must be less than 1") in messages(
        lambda c: c.tables["dcline"][0].__setitem__(16,1))
    assert ("dcline","fbus","bus 99 does not exist") in messages(
        lambda c: c.tables["dcline"][0].__setitem__(0,99))
    assert ("dclinecost",None,"3 cost rows for 4 DC lines (need 4)") in messages(
        lambda c: c.tables["dclinecost"].pop())
    assert ("dcline","Pf","Pf is outside Pmin..Pmax (power flow uses Pf as given)") in messages(
        lambda c: c.tables["dcline"][0].__setitem__(3,50))
    with pytest.raises(CaseError,match="does not exist"):
        case.set_value("dcline",0,"tbus",99)

def test_edits_keep_references(case):
    """Bus renumbering, bus deletion and DC line add/delete keep dcline and dclinecost aligned"""
    case.set_value("bus",2,"bus_i",33) # bus 30
    assert case.tables["dcline"][0][0] == 33
    with pytest.raises(CaseError,match="1 DC line"):
        case.delete_rows("bus",[2])
    removed = case.delete_rows("bus",[2],cascade=True)
    assert removed["dcline"] == 1 and removed["dclinecost"] == 1
    assert len(case.tables["dcline"]) == len(case.tables["dclinecost"]) == 3
    case.add_row("dcline")
    assert len(case.tables["dclinecost"]) == 4
    removed = case.delete_rows("dcline",[0,1])
    assert removed["dcline"] == 2 and removed["dclinecost"] == 2
    assert case.validate() == []

def test_dclinecost_model_change(case):
    """Changing a DC line cost model updates ncost, as for gencost"""
    assert case.set_value("dclinecost",3,"model",1)
    assert case.tables["dclinecost"][3][3] == 1
    assert case.set_value("dclinecost",3,"costs","0,0,10,73")
    assert case.tables["dclinecost"][3][3] == 2
