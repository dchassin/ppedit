"""Tests for the cvxopf OPF solvers (`CVXOPF-*` in `OPF_SOLVERS`; skipped
when cvxopf is not installed; fast, no network)"""

import os

import pytest

from ppedit import Case
from ppedit.case import CaseError, OPF_SOLVERS, solvers

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
    assert all(x is None for t in ("bus","gen","dcline") for row in res["extended"][t] for x in row)
    if algorithm == "CVXOPF-1NODE":
        assert res["branch"] == []
        assert all(x is None for row in res["extended"]["branch"] for x in row)
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

def test_options_preparation():
    """Both numerical preparations reach the same case9 optimum (issue #88)"""
    case = Case.builtin("case9")
    for algorithm in CVXOPF:
        default = case.solve("opf",algorithm)
        prepared = case.solve("opf",algorithm,{"preparation":"recommended"})
        assert prepared["success"] and prepared["options"]["preparation"] == "recommended"
        assert prepared["objective"] == pytest.approx(default["objective"],rel=1e-4)
        assert "recommended preparation" in prepared["log"]

def test_options_solver_and_verbose():
    """Another CVXPY solver, solver output and solver options reach cvxopf"""
    import cvxpy # pylint: disable=import-outside-toplevel
    case = Case.builtin("case9")
    other = next((x for x in ("SCS","ECOS") if x in cvxpy.installed_solvers()),None)
    if other:
        res = case.solve("opf","CVXOPF-DC",{"solver":other.lower()})
        assert res["success"] and res["options"]["solver"] == other
        assert res["objective"] == pytest.approx(case.solve("opf","CVXOPF-DC")["objective"],rel=1e-3)
    res = case.solve("opf","CVXOPF-AC",{"verbose":True})
    assert res["success"] and "Solver output" in res["log"]
    res = case.solve("opf","CVXOPF-AC",{"solver_options":{"max_iter":2}})
    assert not res["success"] and "max_iter=2" in res["log"]
    assert [m["level"] for m in res["messages"]][-1] == "error"

@pytest.mark.parametrize("algorithm,options,match",[
    ("CVXOPF-AC",{"preparation":"fast"},"preparation"),
    ("CVXOPF-AC",{"solver":"SCS"},"only with IPOPT"),
    ("CVXOPF-DC",{"solver":"NOSUCH"},"not installed"),
    ("CVXOPF-AC",{"solver_options":{"verbose":True}},"cannot be set"),
    ("CVXOPF-AC",{"tolerance":1},"unknown"),
])
def test_options_refused(algorithm,options,match):
    """Invalid options are refused before solving"""
    with pytest.raises(CaseError,match=match):
        Case.builtin("case9").solve("opf",algorithm,options)

def test_options_recommended_needs_native_solver():
    """The recommended preparation solves the convex models only with CLARABEL"""
    case = Case.builtin("case9")
    with pytest.raises(CaseError,match="CLARABEL"):
        case.solve("opf","CVXOPF-DC",{"preparation":"recommended","solver":"SCS"})
    assert case.solve("opf","CVXOPF-DC",{"preparation":"recommended","solver":"clarabel"})["success"]

def test_cvxopf_messages():
    """Data cvxopf does not model and a non-exact SOCP relaxation are warnings (issue #89)"""
    res = _pypower_case("case9Q").solve("opf","CVXOPF-AC")
    msg = next(m for m in res["messages"] if m["message"] == "Reactive power cost rows "
        "are ignored: cvxopf has no reactive power costs")
    assert msg["level"] == "warning"
    assert [(c["table"],c["row"]) for c in msg["detail"]["constraints"]] == [("gencost",n) for n in (3,4,5)]
    res = Case.builtin("case9").solve("opf","CVXOPF-SOCP")
    assert res["success"] and all(m["level"] == "warning" for m in res["messages"])

def test_socp_detail():
    """A non-exact SOCP relaxation lists the relaxed constraints that fail and the
    AC constraints its recovered voltages violate, on case rows (decision #53)"""
    res = Case.builtin("case9").solve("opf","CVXOPF-SOCP")
    msg = next(m for m in res["messages"] if m["message"].startswith("SOCP relaxation"))
    detail = msg["detail"]
    assert "lower bound" in detail["explain"]
    rows = detail["constraints"]
    assert rows and all(set(c) == {"constraint","element","table","row","value"} for c in rows)
    case = Case.builtin("case9")
    for c in rows:
        assert c["table"] in ("bus","branch","gen") and 0 <= c["row"] < len(case.tables[c["table"]])
        if c["table"] == "branch":
            f,t = case.tables["branch"][c["row"]][:2]
            assert c["element"] == f"branch {c['row']+1} ({f}-{t})"
    assert any("cone" in c["constraint"] for c in rows)
    assert any("power balance" in c["constraint"] for c in rows)

def test_rate_a_warnings_grouped():
    """cvxopf's rateA = 0 warnings become one message naming case branches and buses"""
    case = Case.builtin("case9")
    for n in (0,3):
        case.set_value("branch",n,"rateA",0)
    res = case.solve("opf","CVXOPF-DC")
    msgs = [m for m in res["messages"] if "rateA = 0" in m["message"]]
    assert len(msgs) == 1 and msgs[0]["message"].startswith("cvxopf warning: 2 branch(es)")
    rows = msgs[0]["detail"]["constraints"]
    assert [(c["table"],c["row"],c["element"]) for c in rows] == \
        [("branch",0,"branch 1 (1-4)"),("branch",3,"branch 4 (3-6)")]
    assert "unlimited" in msgs[0]["detail"]["explain"]

def test_dcline_note_detail():
    """Notes on DC line data cvxopf does not model refer to the DC line rows"""
    case = _pypower_case("t/t_case9_dcline")
    res = case.solve("opf","CVXOPF-DC")
    loss0 = next(m for m in res["messages"] if "loss0" in m["message"])
    assert [(c["table"],c["row"]) for c in loss0["detail"]["constraints"]] == [("dcline",0)]
    res = case.solve("opf","CVXOPF-1NODE")
    msg = next(m for m in res["messages"] if "copper-plate" in m["message"])
    live = [n for n,d in enumerate(case.tables["dcline"]) if d[2] == 1]
    assert [c["row"] for c in msg["detail"]["constraints"]] == live

def test_describe_constraint():
    """CVXPY constraint numbers are described by the quantities they relate"""
    from cvxopf import build_opf # pylint: disable=import-outside-toplevel
    from ppedit._cvxopf import _describe_constraint # pylint: disable=import-outside-toplevel
    build = build_opf(Case.builtin("case9").to_ppc(arrays=True),formulation="ac")
    text = _describe_constraint(build,5)
    assert "branch flow equations" in text and "voltage angles" in text
    assert "not identified" in _describe_constraint(build,10**6)

def test_solvers_lists_options():
    """`solvers()` gives the cvxopf option choices"""
    cvx = solvers()["cvxopf"]
    assert cvx["preparation"] == ["default","recommended"] and "CLARABEL" in cvx["solvers"]
