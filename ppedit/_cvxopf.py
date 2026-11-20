"""@private OPF with cvxopf (github.com/cvxgrp/cvxopf), for `Case.solve`

`runopf(ppc,formulation,log)` solves a PYPOWER case (numpy arrays, isolated
buses already set to type NONE) with one cvxopf network formulation and
returns the solution in the layout of `pypower.runopf` results (`bus`
with 17 columns, `gen` with 25, `branch` with 21, `dcline` with 19, plus
`f`, `success`, `et`, `baseMVA`), so `Case.solve` and `pypower.printpf`
read it like a PYPOWER solution. Values cvxopf does not give are left as
in the case (voltages of unsolved buses) or zero (multipliers, `lam_P`);
`Case.solve` reports the multipliers and prices as unknown. `flows` is
false when the formulation has no branch flows (`singlenode_dc`).

How the case is passed:

  - Buses of type NONE are removed, with the generators on them and the
    branches touching them (cvxopf has no isolated-bus type).

  - Reactive power cost rows (`gencost` with twice as many rows as `gen`)
    are dropped: cvxopf has no reactive power costs.

  - In-service DC lines become `cvxopf.HVDCLink` objects with
    `p_min_mw = -Pmax` and `p_max_mw = -Pmin`: an HVDCLink bounds the
    power injected at its from bus, while PYPOWER's `Pf` is the power
    taken from it. (`cvxopf.hvdc_from_dcline` uses `Pmin..Pmax` as given,
    which runs PYPOWER DC lines in the reverse direction.) `loss1` is the
    proportional loss; `loss0`, reactive power limits and the terminal
    voltage set points `Vf`, `Vt` are not modeled by cvxopf. `dclinecost`
    must be polynomial (model 2) of degree 2 or less; cvxopf applies it to
    `|Pf|`. The copper-plate model (`singlenode_dc`) ignores DC lines.

Sparse derivatives (issue #122): every formulation is built with
`automatic_sparse_dispatch=True`, so CVXPY keeps its density threshold
(`cvxpy.settings.SPARSE_DENSITY_THRESHOLD`) during the build and the
solve. cvxopf's AC default sets it to 0 instead, which makes CVXPY's
derivative engine treat the sparse injection matrices as dense. IPOPT
then factors a far denser KKT system: case300 runs in about 13 s
instead of 55 s with the threshold kept, with the same iterations and
objective. Convex formulations ignore the flag.

Solve options (`solve_options`, issue #88; decision #48):

  - `preparation`: `default` builds with cvxopf's defaults (original
    coordinates, no `NumericalPreparation`); `recommended` builds with
    `OPFOptions(numerical_preparation=NumericalPreparation(**RECOMMENDED[formulation]))`,
    the profile the cvxopf authors recommend for each network model with
    `build_opf`. Prepared execution runs only with CLARABEL (convex
    models) or IPOPT (`ac`).

  - `solver`: CVXPY solver name for the convex models (`socp`,
    `lossy_dc`, `singlenode_dc`; default CLARABEL); `ac` always uses IPOPT.

  - `verbose`: write the solver's own output to `log`.

  - `solver_options`: other keyword arguments for `OPFBuild.solve` (CVXPY
    and solver options such as IPOPT `tol` and `max_iter`, or CLARABEL
    `tol_gap_rel`), passed as given.

Notes about data cvxopf does not model and the warnings cvxopf issues while
building and solving are written to `log` and returned in `warnings`, each
as `{"message", "detail"}`. `detail` (or `None`) says what the warning
means physically and lists the constraints involved (decision #53):

  - `explain`: what the warning means for the network and the solution

  - `constraints`: at most `MAX_CONSTRAINTS` rows of `constraint` (the
    constraint, as cvxopf builds it or as the relaxation leaves it out),
    `element` (a label such as `branch 3 (1-4)`, or `None`), `table` and
    `row` (the case table and row index of the element, or `None`), and
    `value` (the violation or data value, or `None`)

  - `more`: how many further constraints are not listed

cvxopf reports branches and buses by its internal zero-based indexes;
details (and the rewritten messages) use case rows and bus numbers.
Warnings cvxopf issues once per element (`rateA` = 0 branches, slow CVXPY
constraints) are grouped into one message.
"""

import contextlib
import os
import re
import sys
import tempfile
import time
import warnings
import numpy as np

FORMULATIONS = ("ac","socp","lossy_dc","singlenode_dc")
"""cvxopf network formulations `runopf` accepts"""

PREPARATIONS = ("default","recommended")
"""Numerical preparation choices (`preparation` solve option)"""

RECOMMENDED = {
    "singlenode_dc":{"exact_fixed_boxes":True,"canonical_scaling":"joint5"},
    "lossy_dc":{"exact_fixed_boxes":True,"canonical_scaling":"joint5"},
    "socp":{"normalize_device_limits":True,"exact_fixed_boxes":True,"canonical_scaling":"joint5"},
    "ac":{"normalize_device_limits":True,"exact_fixed_boxes":True,"cost_coordinates":True},
}
"""`NumericalPreparation` fields of the `recommended` preparation for each
formulation built with `build_opf` (from the cvxopf authors in issue #88;
`joint5` is five passes of joint objective and constraint scaling; `ac`
keeps the `hourly` objective assembly, since `component_first` applies to
the time-vectorized builder)"""

MAX_CONSTRAINTS = 20
"""Most constraints listed in a warning's `detail`"""

RESERVED = ("solver","verbose","nlp")
"""Solve keyword arguments `solver_options` may not set (ppedit sets them)"""

def solve_options(options:dict|None,formulation:str) -> dict:
    """Check the cvxopf solve `options` (see the module docstring) for
    `formulation` and return them complete (`preparation`, `solver` or
    `None` for cvxopf's default, `verbose`, `solver_options`)

    Exceptions
    ----------

      - `ValueError`: an unknown option or value, a solver that is not
        installed or cannot run `formulation`, or a solver other than
        CLARABEL/IPOPT with the `recommended` preparation
    """
    if formulation not in FORMULATIONS:
        raise ValueError(f"cvxopf formulation must be one of {', '.join(FORMULATIONS)}")
    options = dict(options or {})
    unknown = set(options) - {"preparation","solver","verbose","solver_options"}
    if unknown:
        raise ValueError(f"unknown cvxopf option(s): {', '.join(sorted(unknown))}")
    out = {"preparation":options.get("preparation") or "default",
        "solver":(options.get("solver") or "").strip().upper() or None,
        "verbose":bool(options.get("verbose",False)),
        "solver_options":dict(options.get("solver_options") or {})}
    if out["preparation"] not in PREPARATIONS:
        raise ValueError(f"cvxopf preparation must be one of {', '.join(PREPARATIONS)}")
    native = "IPOPT" if formulation == "ac" else "CLARABEL"
    if out["solver"] == native:
        out["solver"] = None
    if out["solver"]:
        if formulation == "ac":
            raise ValueError("cvxopf full AC (ac) solves only with IPOPT")
        import cvxpy # pylint: disable=import-outside-toplevel
        if out["solver"] not in cvxpy.installed_solvers():
            raise ValueError(f"CVXPY solver {out['solver']} is not installed "
                f"(installed: {', '.join(cvxpy.installed_solvers())})")
        if out["preparation"] == "recommended":
            raise ValueError(f"the recommended cvxopf preparation solves only with {native}; "
                "choose the default preparation to use another solver")
    bad = [k for k in out["solver_options"] if k in RESERVED or not str(k).isidentifier()]
    if bad:
        raise ValueError(f"solver option(s) {', '.join(map(str,bad))} cannot be set "
            f"(not names, or set by ppedit: {', '.join(RESERVED)})")
    return out

@contextlib.contextmanager
def _capture_output(log,enabled):
    """Copy what the solver writes to file descriptors 1 and 2 (IPOPT
    writes from C, CVXPY's verbose log goes to `sys.stderr`) into `log`"""
    if not enabled:
        yield
        return
    with tempfile.TemporaryFile(mode="w+b") as tmp:
        for f in (sys.__stdout__,sys.__stderr__):
            if f:
                f.flush()
        saved = [os.dup(1),os.dup(2)]
        os.dup2(tmp.fileno(),1)
        os.dup2(tmp.fileno(),2)
        try:
            yield
        finally:
            for f in (sys.__stdout__,sys.__stderr__):
                if f:
                    f.flush()
            os.dup2(saved[0],1)
            os.dup2(saved[1],2)
            for fd in saved:
                os.close(fd)
            tmp.seek(0)
            text = tmp.read().decode(errors="replace")
            log.write("\nSolver output\n-------------\n" + text + ("" if text.endswith("\n") else "\n"))

def _hvdc_links(ppc,note):
    """Return the cvxopf `HVDCLink` list for the in-service DC lines and
    their row numbers in `ppc["dcline"]`"""
    from cvxopf import HVDCLink # pylint: disable=import-outside-toplevel
    links,rows = [],[]
    costs = ppc.get("dclinecost")
    for n,d in enumerate(ppc["dcline"]):
        if d[2] == 0:
            continue
        cost = (0.0,0.0,0.0)
        if costs is not None and n < len(costs):
            c = costs[n]
            if int(c[0]) != 2 or int(c[3]) > 3:
                raise ValueError(f"DC line {n+1}: cvxopf needs a polynomial (model 2) "
                    "dclinecost of degree 2 or less")
            coeffs = [float(x) for x in reversed(c[4:4+int(c[3])])] + [0.0,0.0,0.0]
            cost = tuple(coeffs[:3])
        if d[15] != 0:
            note(f"DC line {n+1}: loss0 = {d[15]:g} MW is not modeled by cvxopf",
                _detail("dcline_loss0",[_row("Pt = Pf - loss1 Pf (cvxopf) instead of "
                "Pt = Pf - (loss0 + loss1 Pf)",f"dcline {n+1}","dcline",n,f"loss0 = {d[15]:g} MW")]))
        links.append(HVDCLink(from_bus=int(d[0]),to_bus=int(d[1]),
            p_min_mw=-float(d[10]),p_max_mw=-float(d[9]),
            loss_percent=100*float(d[16]),cost_coeffs=cost,device_id=f"dcline-{n+1}"))
        rows.append(n)
    if links:
        note("DC lines: cvxopf models unity power factor terminals (no Qf, Qt) "
            "and does not hold Vf, Vt",_detail("dcline_terminals",
            [_row(f"Qf = Qt = 0; Vf = {ppc['dcline'][n][7]:g}, Vt = {ppc['dcline'][n][8]:g} "
            "pu not held",f"dcline {n+1}","dcline",n) for n in rows]))
    return links,rows

EXPLAIN = {
    "reactive_cost":"PYPOWER cases can give a second set of gencost rows that price "
        "generator reactive power. cvxopf's objective has real power costs only, so these "
        "rows are dropped: the dispatch does not account for the cost of reactive power and "
        "the objective leaves it out. Reactive power limits (Qmin, Qmax) still apply.",
    "dcline_loss0":"loss0 is a DC line's fixed converter loss, drawn whenever the line is "
        "in service. cvxopf models only the loss proportional to the flow (loss1), so the "
        "line delivers loss0 MW more at its to bus than in PYPOWER, and total losses are "
        "lower by that amount.",
    "dcline_terminals":"In PYPOWER each DC line terminal also acts like a generator that "
        "supplies reactive power within its Qmin..Qmax limits and holds its bus voltage at "
        "Vf or Vt (a PV bus). cvxopf models the converters at unity power factor: they "
        "exchange no reactive power, and the terminal bus voltages follow from the rest of "
        "the network instead of being held.",
    "dcline_copper":"The copper-plate model treats the whole system as one bus: there is "
        "no network, so neither AC branches nor DC lines carry flow, and generation only "
        "has to match total load and losses are zero.",
    "rate_a":"PYPOWER treats rateA = 0 as an unlimited branch. The lossy network-flow "
        "model needs a finite bound on every branch flow, so cvxopf uses a large "
        "placeholder limit (branch_limit_sentinel) for these branches. The branch stays "
        "effectively unlimited unless its flow approaches that value, so this is not a data "
        "error; give the branch a rateA to make its thermal limit count.",
    "subexpressions":"This note is about how long CVXPY takes to convert the problem into "
        "the solver's form, not about the network or the solution: the listed constraint "
        "groups are built element by element, which compiles slowly on large cases. "
        "Results are not affected. Constraint numbers are CVXPY's (zero-based) positions in "
        "the problem's constraint list.",
    "hvdc_straddle":"The loss on a DC line depends on its flow direction. When Pmin < 0 < "
        "Pmax the line may flow either way, and a convex model cannot represent a loss that "
        "switches with direction, so cvxopf solves this line as lossless (Pt = Pf).",
    "pwl_nonconvex":"A piecewise-linear cost whose segment slopes decrease is nonconvex "
        "(marginal cost falls as output rises). A convex solver cannot represent it, so "
        "cvxopf uses the lower convex hull of the breakpoints, which lowers the cost between "
        "the breakpoints it skips. The dispatch can differ from PYPOWER's and the objective "
        "can be below the case's cost curve.",
    "inaccurate":"The solver stopped close to, but not within, its convergence tolerances "
        "(status optimal_inaccurate). The dispatch is approximately optimal, and power "
        "balance and limits may be violated by small amounts. The recommended preparation "
        "or a higher iteration limit (Settings, CVXOPF solve options) may give an accurate "
        "solution.",
    "cost_accuracy":"cvxopf checks, after an AC solve with cost coordinates (recommended "
        "preparation), that the solver's objective matches the generator cost curves "
        "evaluated at the dispatch. That check failed: the dispatch and solver status are "
        "kept, but the objective may not equal the true cost. The default preparation "
        "avoids cost coordinates.",
    "socp":"The SOCP model is a convex relaxation of the AC power flow equations. For each "
        "pair of connected buses it replaces the voltage product W_ij = V_i V_j* by a "
        "variable bounded only by the cone |W_ij|^2 <= w_i w_j (w = Vm^2), and it does not "
        "require voltage angle differences to add up to zero around each loop of a meshed "
        "network. The solution is exact only when every cone holds with equality and every "
        "loop closes. Otherwise no set of bus voltages produces these flows: the objective "
        "is a lower bound on the AC OPF cost, and Vm, Va and the flows are approximations. "
        "The rows below are the relaxed constraints that are not met, then the AC "
        "constraints the recovered voltages violate (worst element of each). Solve with "
        "CVXOPF-AC for an AC-feasible dispatch.",
}
"""Physical meaning of each kind of cvxopf warning (`detail["explain"]`)"""

VARIABLES = {
    "theta":"bus voltage angles","v":"bus voltage magnitudes",
    "w":"squared bus voltage magnitudes","W_re":"voltage products of connected buses (real)",
    "W_im":"voltage products of connected buses (imaginary)",
    "P_vec":"real power terms of the bus admittance matrix",
    "Q_vec":"reactive power terms of the bus admittance matrix",
    "p":"net real power injections at buses","q":"net reactive power injections at buses",
    "Pg":"generator real power","Qg":"generator reactive power",
    "branch_p_from_pu":"branch real power flows at the from bus",
    "branch_q_from_pu":"branch reactive power flows at the from bus",
    "branch_p_to_pu":"branch real power flows at the to bus",
    "branch_q_to_pu":"branch reactive power flows at the to bus",
    "p_flows":"branch real power flows","p_hvdc_in":"DC line power at the from bus",
    "p_hvdc_out":"DC line power at the to bus",
}
"""Physical meaning of cvxopf's CVXPY variables, to describe a constraint"""

def _detail(kind,constraints=(),explain=None) -> dict:
    """Return a warning `detail` of `kind` (see the module docstring)"""
    rows = list(constraints)
    return {"explain":explain or EXPLAIN[kind],"constraints":rows[:MAX_CONSTRAINTS],
        "more":max(0,len(rows)-MAX_CONSTRAINTS)}

def _row(constraint,element=None,table=None,row=None,value=None) -> dict:
    """Return one `detail["constraints"]` row"""
    return {"constraint":constraint,"element":element,"table":table,
        "row":None if row is None else int(row),"value":value}

class _Elements:
    """Map cvxopf's internal indexes to case rows and labels"""

    def __init__(self,bus,gen,branch,live,gkeep,bkeep,gencost):
        self.bus,self.gen,self.branch = bus,gen,branch
        self.gencost = gencost # real power cost rows of the kept generators
        self.brow = np.flatnonzero(live) # internal bus -> case bus row
        self.grow = np.flatnonzero(gkeep)
        self.lrow = np.flatnonzero(bkeep)

    def bus_id(self,k):
        """Bus number of internal bus `k`"""
        return int(self.bus[self.brow[k],0])

    def bus_ref(self,k):
        """(label, table, row) of internal bus `k`"""
        return f"bus {self.bus_id(k)}","bus",int(self.brow[k])

    def branch_ref(self,e):
        """(label, table, row) of internal branch `e`"""
        n = int(self.lrow[e])
        return f"branch {n+1} ({int(self.branch[n,0])}-{int(self.branch[n,1])})","branch",n

    def gen_ref(self,k):
        """(label, table, row) of internal generator `k`"""
        n = int(self.grow[k])
        return f"gen {n+1} @{int(self.gen[n,0])}","gen",n

    def pair_refs(self,i,j):
        """(label, table, row) of the case branches joining internal buses `i` and `j`"""
        a,b = self.bus_id(i),self.bus_id(j)
        refs = [self.branch_ref(e) for e,n in enumerate(self.lrow)
            if {int(self.branch[n,0]),int(self.branch[n,1])} == {a,b}]
        return refs or [(f"buses {a}-{b}",None,None)]

def _describe_constraint(build,n) -> str:
    """Describe CVXPY constraint `n` of `build` by the variables it involves"""
    try:
        con = build.prob.constraints[n]
        names = sorted({v.name() for v in con.variables()})
    except Exception: # pylint: disable=broad-exception-caught
        return "not identified (CVXPY numbering of a prepared problem)"
    size = int(np.prod(con.shape)) if con.shape else 1
    kind = type(con).__name__.lower()
    what = "; ".join(VARIABLES.get(x,x) for x in names)
    if any(x.startswith("branch_") for x in names) and {"theta","v"} <= set(names):
        return f"{size} AC branch flow equations (pi model): {what}"
    if {"P_vec","theta","v"} <= set(names) or {"Q_vec","theta","v"} <= set(names):
        return f"{size} AC power flow terms from voltages: {what}"
    return f"{kind} over {size} element(s): {what}"

def _pwl_nonconvex(gencost,elements) -> list:
    """Constraint rows for the in-service piecewise-linear costs with decreasing slopes"""
    rows = []
    for k,c in enumerate(gencost):
        if int(c[0]) != 1 or int(c[3]) < 3:
            continue
        pts = np.asarray(c[4:4+2*int(c[3])],dtype=float).reshape(-1,2)
        slopes = np.diff(pts[:,1]) / np.diff(pts[:,0])
        if np.any(np.diff(slopes) < -1e-12):
            label,_,n = elements.gen_ref(k)
            rows.append(_row("cost curve replaced by its lower convex hull",
                label.replace("gen","gencost for gen"),"gencost",n,
                "segment slopes " + ", ".join(f"{x:.4g}" for x in slopes) + " $/MWh"))
    return rows

def _socp_detail(build,res,recovered,elements) -> dict:
    """Detail of a non-exact SOCP relaxation: relaxed constraints that fail, then
    the worst AC violation of each kind at the recovered voltages"""
    rows = []
    pairs = np.asarray(build.data["voltage_product_pairs"])
    tol = recovered.get("tolerances") or {}
    if recovered.get("edge_tight") is False:
        from cvxopf import audit_socp_relaxation # pylint: disable=import-outside-toplevel
        audit = audit_socp_relaxation(build,res) or {}
        gaps = np.atleast_2d((audit.get("edge_gaps") or {})["normalized_signed"])[0]
        for k in np.argsort(-np.abs(gaps)):
            if abs(gaps[k]) <= tol.get("rank_relative",1e-5):
                break
            for label,table,row in elements.pair_refs(*pairs[k]):
                rows.append(_row("|W_ij|^2 = w_i w_j, relaxed to <= (voltage product cone)",
                    label,table,row,f"relative gap {gaps[k]:.3g}"))
    cycle = recovered.get("cycle_angles")
    if cycle is not None and np.size(cycle):
        errs = np.atleast_2d(cycle)[0]
        edges = np.asarray(recovered["non_tree_edges"])
        for k in np.argsort(-np.abs(errs)):
            if abs(errs[k]) <= tol.get("cycle_angle",1e-5):
                break
            for label,table,row in elements.pair_refs(*pairs[edges[k]]):
                rows.append(_row("angle differences around the loop this branch closes sum to "
                    "zero (theta_i - theta_j = angle of W_ij); dropped by the relaxation",
                    label,table,row,f"{np.degrees(errs[k]):.3g} deg"))
    ac = recovered.get("ac_audit") or {}
    checks = {"p_balance":("real power balance at the bus (recovered voltages)","bus"),
        "q_balance":("reactive power balance at the bus (recovered voltages)","bus"),
        "voltage_bounds":("Vmin^2 <= Vm^2 <= Vmax^2","bus"),
        "voltage_setpoints":("Vm = Vg at a PV or reference bus","bus"),
        "branch_from":("|S| <= rateA at the from bus (recovered voltages)","branch"),
        "branch_to":("|S| <= rateA at the to bus (recovered voltages)","branch"),
        "generator_p":("Pmin <= Pg <= Pmax","gen"),"generator_q":("Qmin <= Qg <= Qmax","gen")}
    for key,(text,table) in checks.items():
        r = (ac.get("residuals") or {}).get(key)
        if not r or r.get("passed") is not False or not r.get("worst"):
            continue
        k = r["worst"]["identity"]
        label,table,row = {"bus":elements.bus_ref,"branch":elements.branch_ref,
            "gen":elements.gen_ref}[table](int(k))
        rows.append(_row(text,label,table,row,f"{r['maximum']:.4g} {r['units']} "
            + ("mismatch" if key.endswith("balance") or key == "voltage_setpoints"
               else "over the limit")))
    return _detail("socp",rows)

def _cvxopf_warnings(caught,build,elements,rows) -> list:
    """Return `(message, detail)` for the distinct cvxopf warnings in `caught`,
    with internal indexes replaced by case rows and per-element warnings grouped"""
    out,seen = [],set()
    rate,sentinel,slow = [],None,[]
    for w in caught:
        text = str(w.message).strip()
        if text in seen or not (issubclass(w.category,UserWarning)
                or w.category.__name__ == "CostAccuracyWarning"):
            continue
        seen.add(text)
        m = re.match(r"Branch (\d+) \(bus \d+ -> \d+\) has rateA=0; substituting "
            r"branch_limit_sentinel=([0-9.eE+-]+) MW",text)
        if m:
            rate.append(int(m[1]))
            sentinel = float(m[2])
            continue
        m = re.match(r"Constraint #(\d+) contains too many subexpressions",text)
        if m:
            slow.append(int(m[1]))
            continue
        message,detail = f"cvxopf warning: {text}",None
        m = re.match(r"HVDC link (\d+) at step \d+: box \[([^,]+), ([^\]]+)\] straddles zero "
            r"with loss_percent=([0-9.eE+-]+)",text)
        if m and int(m[1]) < len(rows):
            n = rows[int(m[1])]
            message = f"cvxopf warning: DC line {n+1} can flow either way (Pmin < 0 < Pmax); " \
                "solved without loss"
            detail = _detail("hvdc_straddle",[_row("Pt = Pf (lossless) instead of "
                "Pt = Pf - loss1 |Pf|",f"dcline {n+1}","dcline",n,
                f"loss1 = {float(m[4])/100:g}")])
        elif "piecewise-linear cost is nonconvex" in text:
            detail = _detail("pwl_nonconvex",_pwl_nonconvex(elements.gencost,elements))
        elif text.startswith("Solution may be inaccurate"):
            detail = _detail("inaccurate")
        elif w.category.__name__ == "CostAccuracyWarning":
            detail = _detail("cost_accuracy")
        out.append((message,detail))
    if rate:
        out.insert(0,(f"cvxopf warning: {len(rate)} branch(es) with rateA = 0 limited to "
            f"{sentinel:g} MW (lossy network flow model)",_detail("rate_a",[
            _row(f"|P| <= {sentinel:g} MW (rateA = 0 in the case)",*elements.branch_ref(e))
            for e in rate])))
    if slow:
        out.append((f"CVXPY: constraint(s) {', '.join(f'#{n}' for n in slow)} are slow to "
            "compile (too many subexpressions); results are not affected",
            _detail("subexpressions",[_row(f"#{n}: {_describe_constraint(build,n)}")
            for n in slow])))
    return out

def _pad(a,ncol):
    """Return a float copy of the 2-D array `a` with at least `ncol` columns (zero filled)"""
    a = np.asarray(a,dtype=float).reshape(len(a),-1)
    out = np.zeros((a.shape[0],max(ncol,a.shape[1])))
    out[:,:a.shape[1]] = a
    return out

def runopf(ppc:dict,formulation:str,log,options:dict|None=None) -> dict:
    """Solve `ppc` with the cvxopf `formulation` (see the module docstring)

    Arguments
    ---------

      - `ppc`: PYPOWER case of numpy arrays (`Case.to_ppc(arrays=True)`)

      - `formulation`: one of `FORMULATIONS`

      - `log`: text stream for notes and cvxopf warnings

      - `options`: solve options (see the module docstring and `solve_options`)

    Returns
    -------

      - `dict`: PYPOWER-style OPF results (see the module docstring), plus
        `status` (the solver status) and `warnings` (notes on data cvxopf
        does not model, cvxopf's warnings and relaxation checks that failed,
        as `{"message", "detail"}`)

    Exceptions
    ----------

      - `ValueError`: data or options cvxopf cannot use; cvxopf's own errors pass through
    """
    from cvxopf import build_opf, extract_results, ACCEPTED_SOLVER_STATUSES, \
        OPFOptions, NumericalPreparation # pylint: disable=import-outside-toplevel
    opts = solve_options(options,formulation)
    notes = []
    def note(text,detail=None):
        notes.append({"message":text,"detail":detail})
        log.write(text + "\n")
    start = time.time()
    bus,gen,branch = ppc["bus"],ppc["gen"],ppc["branch"]
    live = bus[:,1] != 4
    ids = set(bus[live,0].astype(int))
    gkeep = np.array([int(b) in ids for b in gen[:,0]],dtype=bool)
    bkeep = np.array([int(f) in ids and int(t) in ids for f,t in branch[:,:2]],dtype=bool)
    gencost = ppc["gencost"]
    if len(gencost) == 2*len(gen):
        note("Reactive power cost rows are ignored: cvxopf has no reactive power costs",
            _detail("reactive_cost",[_row("cost of Qg left out of the objective",
            f"gencost for Q of gen {n+1} @{int(gen[n,0])}","gencost",len(gen)+n)
            for n in np.flatnonzero(gkeep)]))
        gencost = gencost[:len(gen)]
    case = {"version":"2","baseMVA":float(ppc["baseMVA"]),"bus":bus[live],
        "gen":gen[gkeep],"branch":branch[bkeep],"gencost":gencost[gkeep]}
    links,rows = [],[]
    if "dcline" in ppc:
        if formulation == "singlenode_dc":
            note("DC lines are ignored by the copper-plate model (singlenode_dc)",
                _detail("dcline_copper",[_row("not modeled (no network)",f"dcline {n+1}",
                "dcline",n) for n,d in enumerate(ppc["dcline"]) if d[2] != 0]))
        else:
            links,rows = _hvdc_links(ppc,note)
    build_options = None
    if opts["preparation"] == "recommended":
        build_options = OPFOptions(numerical_preparation=NumericalPreparation(**RECOMMENDED[formulation]))
    kwargs = dict(opts["solver_options"])
    if opts["solver"]:
        kwargs["solver"] = opts["solver"]
    if opts["verbose"]:
        kwargs["verbose"] = True
    log.write(f"cvxopf {formulation}: {opts['preparation']} preparation"
        + "".join(f", {k}={v}" for k,v in (RECOMMENDED[formulation].items()
            if opts["preparation"] == "recommended" else ()))
        + f"; solver {opts['solver'] or ('IPOPT' if formulation == 'ac' else 'CLARABEL')}"
        + "".join(f", {k}={v!r}" for k,v in opts["solver_options"].items()) + "\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        build = build_opf(case,formulation=formulation,hvdc=links or None,options=build_options,
            automatic_sparse_dispatch=True) # issue #122: keep CVXPY's sparse derivatives
        with _capture_output(log,opts["verbose"]):
            build.solve(**kwargs)
        res = extract_results(build)
        recovered = None
        if formulation == "socp" and res["status"] in ACCEPTED_SOLVER_STATUSES:
            from cvxopf import recover_socp_voltage # pylint: disable=import-outside-toplevel
            recovered = recover_socp_voltage(build,res)
    elements = _Elements(bus,gen,branch,live,gkeep,bkeep,gencost[gkeep])
    for text,detail in _cvxopf_warnings(caught,build,elements,rows):
        note(text,detail)
    success = res["status"] in ACCEPTED_SOLVER_STATUSES
    log.write(f"cvxopf {formulation}: solver status {res['status']}\n")
    out = {"baseMVA":ppc["baseMVA"],"success":success,"et":time.time()-start,
        "bus":_pad(bus,17),"gen":_pad(gen,25),"branch":_pad(branch,21),
        "flows":formulation != "singlenode_dc","status":str(res["status"]),"warnings":notes}
    out["gen"][:,1:3] = 0.0
    out["branch"][:,13:21] = 0.0
    if "dcline" in ppc:
        out["dcline"] = _pad(ppc["dcline"],19)
        out["dcline"][:,3:9] = 0.0
        out["dcline"][:,17:19] = 0.0
        if formulation == "singlenode_dc":
            out["dcline"][:,2] = 0
    if not success:
        return out
    out["f"] = float(res["objective"])
    b,g,br = out["bus"],out["gen"],out["branch"]
    if formulation == "ac":
        b[live,7],b[live,8] = res["Vm"],res["Va_deg"]
    elif formulation == "socp":
        b[live,7],b[live,8] = res["Vm_relaxed"],0.0
        cand = recovered and recovered.get("candidate")
        if cand and cand.get("Va_deg") is not None:
            b[live,8] = np.ravel(cand["Va_deg"])
        exact = recovered and recovered.get("exact_product_recovery")
        feasible = recovered and recovered.get("ac_feasible")
        log.write("SOCP relaxation: Vm is sqrt(w); Va is "
            + ("from cvxopf's spanning-forest voltage recovery" if cand else "not available (0)")
            + "; exact relaxation: " + {True:"yes",False:"no",None:"unknown"}[exact]
            + ", recovered voltages AC feasible: " + {True:"yes",False:"no",None:"unknown"}[feasible] + "\n")
        text = "SOCP relaxation is not exact: Vm and Va are not an AC power flow solution" \
            if exact is False else "SOCP relaxation: the recovered voltages are not AC feasible" \
            if feasible is False else None
        if text:
            try:
                detail = _socp_detail(build,res,recovered,elements)
            except Exception as err: # pylint: disable=broad-exception-caught
                detail = _detail("socp",
                    explain=EXPLAIN["socp"] + f" (constraint check unavailable: {err})")
            notes.append({"message":text,"detail":detail})
    else:
        b[live,7],b[live,8] = 1.0,0.0
        log.write("DC formulation: flat voltages (1 pu) and no angles "
            + ("(lossy_dc is a network-flow model, not a phase-angle DC power flow)\n"
               if formulation == "lossy_dc" else "(copper plate: no network, no branch flows)\n"))
    g[gkeep,1] = res["Pg"]
    if res.get("Qg") is not None:
        g[gkeep,2] = res["Qg"]
    g[g[:,7] == 0,1:3] = 0.0
    if formulation in ("ac","socp"):
        for col,key in zip(range(13,17),("branch_p_from","branch_q_from","branch_p_to","branch_q_to")):
            br[bkeep,col] = res[key]
    elif formulation == "lossy_dc":
        br[bkeep,13],br[bkeep,15] = res["p_flows"],-np.asarray(res["p_flows"])
    br[br[:,10] == 0,13:17] = 0.0
    if rows:
        d = out["dcline"]
        for k,n in enumerate(rows):
            d[n,3] = -float(res["p_hvdc_in"][k])
            d[n,4] = float(res["p_hvdc_out"][k])
            vm = {int(x[0]):x[7] for x in b}
            d[n,7],d[n,8] = vm[int(d[n,0])],vm[int(d[n,1])]
    return out
