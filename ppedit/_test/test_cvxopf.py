"""Tests for the cvxopf OPF solvers (`CVXOPF-*` in `OPF_SOLVERS`; skipped
when cvxopf is not installed; fast, no network)"""

import os

import pytest

from ppedit import Case
from ppedit.case import OPF_SOLVERS, solvers

pytest.importorskip("cvxopf")

CVXOPF = [k for k,v in OPF_SOLVERS.items() if v[3] == "cvxopf"]

def _pypower_case(name):
    import pypower # pylint: disable=import-outside-toplevel
    path = os.path.join(os.path.dirname(pypower.__file__),f"{name}.py")
    if not os.path.exists(path):
        pytest.skip(f"pypower case {name} is not installed")
    return Case.load(path)

def test_offered():
    """All four formulations are offered when cvxopf is installed, AC before DC"""
    opf = solvers()["opf"]
    assert {x["algorithm"] for x in opf} >= set(CVXOPF)
    assert [x["dc"] for x in opf if x["algorithm"] in CVXOPF] == [False,False,True,True]

def test_ac_matches_pips():
    """CVXOPF-AC reaches the PIPS optimum on case9; no prices are reported"""
    case = Case.builtin("case9")
    pips,cvx = case.solve("opf","PIPS"),case.solve("opf","CVXOPF-AC")
    assert cvx["success"] and not cvx["dc"] and cvx["solver"] == "AC: CVXOPF (IPOPT)"
    assert cvx["objective"] == pytest.approx(pips["objective"],rel=1e-5)
    assert cvx["losses_MW"] == pytest.approx(pips["losses_MW"],abs=1e-3)
    for a,b in zip(cvx["gen"],pips["gen"]):
        assert a["Pg"] == pytest.approx(b["Pg"],abs=1e-2)
    for a,b in zip(cvx["bus"],pips["bus"]):
        assert a["Vm"] == pytest.approx(b["Vm"],abs=1e-4)
        assert a["Va"] == pytest.approx(b["Va"],abs=1e-3)
        assert a["lam_P"] is None
    assert all(a["lam_P"] is None for a in cvx["areas"])
    assert "Objective Function Value" in cvx["log"]

@pytest.mark.parametrize("algorithm",CVXOPF)
def test_formulations(algorithm):
    """Every formulation solves case9 and its result can be applied"""
    case = Case.builtin("case9")
    res = case.solve("opf",algorithm)
    assert res["success"] and res["algorithm"] == algorithm
    assert sum(g["Pg"] for g in res["gen"]) == pytest.approx(315+res["losses_MW"],abs=1e-3)
    if res["dc"]:
        assert all(b["Vm"] == 1 and b["Va"] == 0 for b in res["bus"])
        assert all(g["Qg"] == 0 for g in res["gen"]) and res["losses_MW"] == pytest.approx(0,abs=1e-6)
    if algorithm == "CVXOPF-1NODE":
        assert res["branch"] == []
    else:
        assert len(res["branch"]) == len(case.tables["branch"])
    if algorithm == "CVXOPF-DC":
        assert all(b["PT"] == -b["PF"] for b in res["branch"])
    case.apply_result(res)

def test_relaxation_bounds_ac():
    """The SOCP relaxation and DC models cost no more than full AC"""
    case = Case.builtin("case9")
    obj = {k:case.solve("opf",k)["objective"] for k in CVXOPF}
    assert obj["CVXOPF-SOCP"] <= obj["CVXOPF-AC"] + 1e-3
    assert obj["CVXOPF-1NODE"] <= obj["CVXOPF-DC"] + 1e-3

def test_isolated_bus():
    """A bus with no in-service branch is left out of the cvxopf problem"""
    case = Case.builtin("case9")
    case.add_row("bus",[10,1,0,0,0,0,1,1,0,345,1,1.1,0.9])
    res = case.solve("opf","CVXOPF-AC")
    assert res["success"] and len(res["bus"]) == 10
    assert "isolated" in res["log"]

def test_pwl_and_q_costs():
    """Piecewise-linear costs solve; reactive power cost rows are ignored with a note"""
    assert _pypower_case("case30pwl").solve("opf","CVXOPF-AC")["success"]
    res = _pypower_case("case9Q").solve("opf","CVXOPF-AC")
    assert res["success"] and "Reactive power cost rows are ignored" in res["log"]

def test_dcline_direction():
    """DC lines run in PYPOWER's direction: Pf leaves the from bus, Pt = (1 - loss1) Pf"""
    case = _pypower_case(os.path.join("t","t_case9_dcline"))
    res = case.solve("opf","CVXOPF-AC")
    assert res["success"]
    for row,d in zip(case.tables["dcline"],res["dcline"]):
        if not row[2]:
            assert not d["solved"]
            continue
        assert d["solved"] and row[9] - 1e-6 <= d["Pf"] <= row[10] + 1e-6
        assert d["Pt"] == pytest.approx((1-row[16])*d["Pf"],abs=1e-6)
        assert d["mu_Pmax"] is None
    assert "loss0" in res["log"]
    res = case.solve("opf","CVXOPF-1NODE")
    assert res["success"] and not any(d["solved"] for d in res["dcline"])
