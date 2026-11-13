"""Tests for `ppedit.case` (fast, no network)"""

import math
import os

import pytest

from ppedit import Case, CaseError, COLUMNS, builtin_cases

DATA = os.path.join(os.path.dirname(__file__),"data")

@pytest.mark.parametrize("name",builtin_cases())
@pytest.mark.parametrize("fmt,ext",[("py",".py"),("json",".json"),("glm",".glm")])
def test_round_trip(name,fmt,ext):
    """Every built-in case survives a write/read in every format"""
    case = Case.builtin(name)
    again = Case.load(f"x{ext}",case.dumps(fmt))
    assert again.tables == case.tables
    assert again.baseMVA == case.baseMVA

def test_shapes():
    """Rows have one value per column"""
    case = Case.builtin("case14")
    for table,rows in case.tables.items():
        assert all(len(r) == len(COLUMNS[table]) for r in rows)
    assert case.size() == {"bus":14,"gen":5,"branch":20,"gencost":5}

def test_py_not_executed(tmp_path):
    """A .py case is parsed, never run"""
    marker = tmp_path / "ran"
    text = f"""
import os
os.system("touch {marker}")
def evil():
    ppc = {{"version": "2"}}
    ppc["baseMVA"] = 100
    ppc["bus"] = [[1,3,0,0,0,0,1,1,0,230,1,1.1,0.9]]
    ppc["gen"] = [[1,0,0,10,-10,1,100,1,10,0,0,0,0,0,0,0,0,0,0,0,0]]
    ppc["branch"] = []
    open("{marker}","w")
    return ppc
"""
    case = Case.load("evil.py",text)
    assert case.size()["bus"] == 1
    assert not marker.exists()

def test_py_inf_and_arithmetic():
    """Inf and simple arithmetic in arrays are accepted"""
    text = """
from numpy import array, inf
def c():
    ppc = {"version": '2'}
    ppc["baseMVA"] = 100.0
    ppc["bus"] = array([[1, 3, 0, 0, 0, 0, 1, 1, 0, 345, 1, 1.1, 0.9]])
    ppc["gen"] = array([[1, 0, 0, inf, -inf, 1, 100, 1, 250, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]])
    ppc["branch"] = array([[1, 1, 0, 1/2, 0, 0, 0, 0, 0, 0, 1, -360, 360]])
    return ppc
"""
    case = Case.load("c.py",text)
    assert math.isinf(case.tables["gen"][0][3])
    assert case.tables["branch"][0][3] == 0.5
    assert '"Inf"' in case.dumps("json")

def test_py_return_dict_literal():
    """A case function may return the case dict directly (issue #5)"""
    text = """
from numpy import array
def c():
    return {
      'version': 2,
      'baseMVA': 100.0,
      'bus': array([[1, 3, 0, 0, 0, 0, 1, 1, 0, 345, 1, 1.1, 0.9],
                    [2, 1, 10, 5, 0, 0, 1, 1, 0, 345, 1, 1.1, 0.9]]),
      'gen': array([[1, 0, 0, 100, -100, 1, 100, 1, 250, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]),
      'branch': array([[1, 2, 0, 0.1, 0, 0, 0, 0, 0, 0, 1, -360, 360]]),
      'gencost': array([[2, 0, 0, 3, 0.01, 20, 0]]),
    }
"""
    case = Case.load("c.py",text)
    assert case.baseMVA == 100.0
    assert case.size() == {"bus":2,"gen":1,"branch":1,"gencost":1}
    assert case.tables["bus"][1][2] == 10
    assert case.tables["gencost"][0][4] == "0.01,20,0"

def test_glm_import():
    """GLM objects, references, keyword/numeric enums, and notes"""
    case = Case.load(os.path.join(DATA,"case3.glm"))
    assert [r[0] for r in case.tables["bus"]] == [1,2,3]
    assert [r[1] for r in case.tables["bus"]] == [3,2,1]
    assert [r[0] for r in case.tables["gen"]] == [1,2]
    assert [(r[0],r[1]) for r in case.tables["branch"]] == [(1,2),(2,3),(1,3)]
    assert [r[4] for r in case.tables["gencost"]] == ["0.01,20,0","0.02,25,0"]
    assert case.tables["bus"][1][11] == 1.1 # default Vmax
    assert "1 load" in case.notes[0] and "1 powerplant" in case.notes[0]
    assert "# directive" not in case.notes[0]
    assert case.validate() == []

def test_glm_output_matches_module_conventions():
    """GLM output uses module pypower keywords, units, and parent links"""
    text = Case.builtin("case9").dumps("glm")
    assert "module pypower" in text and "baseMVA 100;" in text
    assert "type REF;" in text and "status IN_SERVICE;" in text and "status IN;" in text
    assert 'parent "pp_bus_1";' in text and 'from "pp_bus_1";' in text
    assert "model POLYNOMIAL;" in text and "Pd 90 MW;" in text

def test_bad_extension():
    with pytest.raises(CaseError):
        Case.load("case.txt","")

def test_renumber_bus_cascades():
    """Renumbering a bus renumbers its generator and branch references"""
    case = Case.builtin("case9")
    assert case.set_value("bus",0,"bus_i",100) is True
    assert case.tables["gen"][0][0] == 100
    assert case.tables["branch"][0][0] == 100
    assert case.validate() == []
    with pytest.raises(CaseError):
        case.set_value("bus",1,"bus_i",100) # duplicate

def test_reference_must_exist():
    case = Case.builtin("case9")
    with pytest.raises(CaseError):
        case.set_value("gen",0,"bus",99)
    with pytest.raises(CaseError):
        case.set_value("branch",0,"tbus",99)

def test_invalid_values():
    case = Case.builtin("case9")
    with pytest.raises(CaseError):
        case.set_value("bus",0,"type",7)
    with pytest.raises(CaseError):
        case.set_value("bus",0,"Pd","abc")
    with pytest.raises(CaseError):
        case.set_value("bus",0,"area",1.5)
    with pytest.raises(CaseError):
        case.set_global("baseMVA",0)

def test_delete_bus_requires_cascade():
    case = Case.builtin("case9")
    with pytest.raises(CaseError):
        case.delete_rows("bus",[0])
    removed = case.delete_rows("bus",[0],cascade=True)
    assert removed == {"bus":1,"gen":1,"branch":1,"gencost":1}
    numbers = {r[0] for r in case.tables["bus"]}
    assert all(g[0] in numbers for g in case.tables["gen"])
    assert all(b[0] in numbers and b[1] in numbers for b in case.tables["branch"])

def test_gencost_follows_gen():
    """Adding/deleting generators keeps gencost aligned"""
    case = Case.builtin("case9")
    case.add_row("gen",{"bus":5})
    assert len(case.tables["gencost"]) == len(case.tables["gen"]) == 4
    case.delete_rows("gen",[3])
    assert len(case.tables["gencost"]) == 3
    case = Case.builtin("case9Q") # P and Q costs
    case.delete_rows("gen",[1])
    assert len(case.tables["gencost"]) == 4

def test_costs_update_ncost():
    case = Case.builtin("case9")
    case.set_value("gencost",0,"costs","1,2")
    assert case.tables["gencost"][0][3] == 2
    case.set_value("gencost",0,"model",1)
    assert case.tables["gencost"][0][3] == 1

def test_add_row_defaults():
    case = Case(name="new")
    case.add_row("bus",{"type":3})
    case.add_row("bus")
    assert [r[0] for r in case.tables["bus"]] == [1,2]
    case.add_row("gen")
    assert case.tables["gen"][0][0] == 1 # placed at REF bus
    case.add_row("branch")
    assert case.tables["branch"][0][:2] == [1,2]
    assert case.validate() == []

def test_validate_finds_errors():
    case = Case.builtin("case9")
    case.tables["bus"][0][1] = 1 # no REF
    case.tables["branch"][0][2:4] = [0.0,0.0]
    case.tables["gencost"][0][3] = 5
    messages = " ".join(x["message"] for x in case.validate())
    assert "no reference" in messages
    assert "zero impedance" in messages
    assert "needs 5 values" in messages

def test_solve_pf_and_opf():
    case = Case.builtin("case9")
    pf = case.solve("pf")
    assert pf["success"] and len(pf["bus"]) == 9 and len(pf["branch"]) == 9
    gen = sum(g["Pg"] for g in pf["gen"])
    load = sum(b[2] for b in case.tables["bus"])
    assert gen == pytest.approx(load + pf["losses_MW"],abs=1e-3)
    opf = case.solve("opf")
    assert opf["success"] and opf["objective"] > 0

def test_solve_refuses_errors():
    case = Case.builtin("case9")
    case.tables["bus"][0][1] = 1
    with pytest.raises(CaseError):
        case.solve()

def test_apply_result():
    case = Case.builtin("case9")
    result = case.solve("pf")
    case.apply_result(result)
    assert case.tables["gen"][0][1] == pytest.approx(result["gen"][0]["Pg"])
    assert case.tables["bus"][8][7] == pytest.approx(result["bus"][8]["Vm"])

def test_glm_other_content_noted():
    """Clock, other modules, and directives are reported as not kept"""
    text = """#set suppress_repeat_messages=FALSE
clock { starttime "2020-01-01 00:00:00"; }
module tape;
module pypower { baseMVA 100; solver_method NR; }
object pypower.bus { bus_i 1; type REF; }
object recorder { file "x.csv"; }
"""
    case = Case.load("x.glm",text)
    note = case.notes[0]
    for item in ("1 # directive","1 clock","1 recorder","1 module pypower setting"):
        assert item in note

def test_solve_with_isolated_bus():
    """A bus with no in-service branch does not stop the solve"""
    case = Case.builtin("case9")
    case.add_row("bus",{"Pd":10})
    result = case.solve("pf")
    assert result["success"] and "isolated" in result["log"]
    assert case.tables["bus"][-1][1] == 1 # case unchanged
