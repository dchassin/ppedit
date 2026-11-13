"""Tests for areas (`areas` table and the per-area results; fast, no network)"""

import os

import pytest

from ppedit import Case, CaseError
from ppedit.case import COLUMNS

@pytest.fixture(name="case")
def fixture_case():
    """PYPOWER's 30-bus case: 3 areas with price reference buses 8, 23 and 26"""
    return Case.builtin("case30")

def _messages(case,table="areas"):
    return [x["message"] for x in case.validate() if x["table"] == table]

def test_load(case):
    """areas rows keep the 2 input columns"""
    assert [c["name"] for c in COLUMNS["areas"]] == ["area","price_ref_bus"]
    assert case.tables["areas"] == [[1,8],[2,23],[3,26]]
    assert case.validate() == []

def test_rebuilt_from_bus_areas():
    """A case without an areas table gets one row per bus area"""
    case = Case.builtin("case39") # 3 bus areas, no areas table
    assert [r[0] for r in case.tables["areas"]] == [1,2,3]
    assert case.validate() == []
    ref = [r[0] for r in case.tables["bus"] if r[1] == 3][0]
    assert [ref] == [r[1] for r in case.tables["areas"]
        if r[0] == case.tables["bus"][[b[0] for b in case.tables["bus"]].index(ref)][6]]
    assert Case.builtin("case14").tables["areas"] == [[1,1]]
    assert Case().tables["areas"] == []

def test_default_price_ref_bus():
    """REF bus, else the most in-service generating capacity, else the first bus"""
    case = Case.builtin("case9") # bus 1 REF; gens at 1 (250), 2 (300), 3 (270)
    case.set_value("bus",1,"area",2)
    case.set_value("bus",2,"area",2)
    case.set_value("bus",3,"area",3)
    assert case._price_ref(1) == 1
    assert case._price_ref(2) == 2
    case.set_value("gen",1,"status",0)
    assert case._price_ref(2) == 3
    assert case._price_ref(3) == 4

@pytest.mark.parametrize("fmt,ext",[("py",".py"),("json",".json")])
def test_round_trip(case,fmt,ext):
    """areas survives .py and .json"""
    text = case.dumps(fmt)
    assert '"areas"' in text
    again = Case.load(f"x{ext}",text)
    assert again.tables == case.tables

def test_glm_drops_areas(case):
    """areas is not written to GLM (no class for it); bus areas are kept and
    the rows are rebuilt on reading, with default price reference buses"""
    text = case.dumps("glm")
    assert "3 areas row(s) not written" in text
    again = Case.load("x.glm",text)
    assert again.tables["areas"] == [[1,1],[2,13],[3,27]] # REF bus; most generating capacity
    assert [r[6] for r in again.tables["bus"]] == [r[6] for r in case.tables["bus"]]
    assert "areas row(s) not written" not in again.dumps("glm") # nothing lost

def test_not_passed_to_solver(case):
    """areas is in the case dict but not in the solver arrays"""
    assert case.to_ppc()["areas"] == [[1,8],[2,23],[3,26]]
    assert "areas" not in case.to_ppc(arrays=True)
    assert "areas" not in Case().to_ppc()

def test_renumber_bus(case):
    """Renumbering a price reference bus updates its areas row"""
    assert case.set_value("bus",7,"bus_i",108) is True
    assert case.tables["areas"][0] == [1,108]
    assert case.validate() == []

def test_renumber_area(case):
    """Renumbering an area moves its buses with it; duplicates are refused"""
    before = [r[6] for r in case.tables["bus"]]
    assert case.set_value("areas",1,"area",7) is True
    assert [r[6] for r in case.tables["bus"]] == [7 if a == 2 else a for a in before]
    assert case.validate() == []
    with pytest.raises(CaseError,match="already exists"):
        case.set_value("areas",0,"area",3)

def test_price_ref_bus_must_exist(case):
    """price_ref_bus must name an existing bus"""
    with pytest.raises(CaseError,match="does not exist"):
        case.set_value("areas",0,"price_ref_bus",99)
    assert case.set_value("areas",0,"price_ref_bus",1) is False

def test_add_row(case):
    """A new areas row lists an unlisted bus area and never moves buses"""
    case.set_value("bus",0,"area",4)
    case.set_value("bus",0,"type",3)
    areas = [r[6] for r in case.tables["bus"]]
    index = case.add_row("areas")
    assert case.tables["areas"][index] == [4,1]
    index = case.add_row("areas",{"area":9,"price_ref_bus":2})
    assert case.tables["areas"][index] == [9,2]
    assert [r[6] for r in case.tables["bus"]] == areas
    assert case.default_row("areas")[0] == 10
    with pytest.raises(CaseError,match="already exists"):
        case.add_row("areas",[9,2])
    with pytest.raises(CaseError,match="already exists"):
        case.add_row("areas",{"area":1})
    assert len(case.tables["areas"]) == 5

def test_delete_price_ref_bus(case):
    """Deleting a price reference bus needs cascade, which deletes its areas row"""
    with pytest.raises(CaseError,match="price reference bus of 1 area"):
        case.delete_rows("bus",[7])
    removed = case.delete_rows("bus",[7],cascade=True)
    assert removed["areas"] == 1 and removed["bus"] == 1
    assert case.tables["areas"] == [[2,23],[3,26]]

def test_validate(case):
    """Duplicate areas, bad price reference buses, empty and unlisted areas"""
    areas = case.tables["areas"]
    areas.append([2,23])
    assert "area 2 duplicates row 2" in _messages(case)
    del areas[-1]
    areas[0][1] = 99
    assert "bus 99 does not exist" in _messages(case)
    areas[0][1] = 23
    assert "price reference bus 23 is in area 2, not 1" in _messages(case)
    areas[0][1] = 8
    areas.append([5,1])
    assert "area 5 has no buses" in _messages(case)
    del areas[-1]
    case.set_value("bus",0,"area",6)
    assert "area 6 (1 bus(es)) has no areas row" in _messages(case)
    assert all(x["level"] == "warning" for x in case.validate())

def _check_balance(result):
    assert result["success"]
    for a in result["areas"]:
        balance = a["Pg"] - a["Pd"] - a["shunt_MW"] - a["losses_MW"] - a["export_MW"]
        assert abs(balance) < 1e-4,a
    ties = sum(a["export_MW"] for a in result["areas"])
    assert abs(sum(a["losses_MW"] for a in result["areas"]) + ties - result["losses_MW"]) < 1e-6

@pytest.mark.parametrize("kind",["pf","opf"])
def test_solve(case,kind):
    """Each area balances; area and tie losses add up to the system losses"""
    result = case.solve(kind)
    _check_balance(result)
    assert [a["area"] for a in result["areas"]] == [1,2,3]
    assert [a["buses"] for a in result["areas"]] == [11,10,9]
    assert [a["price_ref_bus"] for a in result["areas"]] == [8,23,26]
    assert abs(sum(a["Pd"] for a in result["areas"]) - sum(r[2] for r in case.tables["bus"])) < 1e-9
    lam = {b["bus_i"]:b["lam_P"] for b in result["bus"]}
    for a in result["areas"]:
        assert a["lam_P"] == (lam[a["price_ref_bus"]] if kind == "opf" else None)
    assert "Areas" in result["log"]

def test_solve_unlisted_area():
    """Bus areas are summed even when they have no areas row; shunts count"""
    case = Case.builtin("case14")
    for n in range(7,14):
        case.set_value("bus",n,"area",2)
    result = case.solve("pf")
    _check_balance(result)
    assert [a["price_ref_bus"] for a in result["areas"]] == [1,None]
    assert sum(a["shunt_MW"] for a in result["areas"]) == 0.0
    case.set_value("bus",8,"Gs",5)
    _check_balance(case.solve("pf"))

def test_solve_isolated_bus():
    """An isolated bus counts toward its area's buses but not its sums"""
    case = Case.builtin("case30")
    case.add_row("bus",{"bus_i":31,"area":2,"Pd":50,"Gs":3})
    case.add_row("gen",{"bus":31,"Pg":40})
    result = case.solve("pf")
    _check_balance(result)
    a = result["areas"][1]
    assert a["buses"] == 11 and abs(a["Pd"] - 56.2) < 1e-9

def test_solve_dcline_tie():
    """A DC line between areas is a tie; within an area its loss is the area's"""
    import pypower # pylint: disable=import-outside-toplevel
    path = os.path.join(os.path.dirname(pypower.__file__),"t","t_case9_dcline.py")
    if not os.path.exists(path):
        pytest.skip("pypower test case t_case9_dcline is not installed")
    case = Case.load(path)
    for n in range(4,9):
        case.set_value("bus",n,"area",2)
    for kind in ("pf","opf"):
        result = case.solve(kind)
        _check_balance(result)
