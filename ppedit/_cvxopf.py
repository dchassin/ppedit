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

Solve options (`solve_options`, issue #88; decision #36k):

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
building and solving are written to `log` and returned in `warnings`.
"""

import contextlib
import os
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
            note(f"DC line {n+1}: loss0 = {d[15]:g} MW is not modeled by cvxopf")
        links.append(HVDCLink(from_bus=int(d[0]),to_bus=int(d[1]),
            p_min_mw=-float(d[10]),p_max_mw=-float(d[9]),
            loss_percent=100*float(d[16]),cost_coeffs=cost,device_id=f"dcline-{n+1}"))
        rows.append(n)
    if links:
        note("DC lines: cvxopf models unity power factor terminals (no Qf, Qt) "
            "and does not hold Vf, Vt")
    return links,rows

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
        does not model, cvxopf's warnings and relaxation checks that failed)

    Exceptions
    ----------

      - `ValueError`: data or options cvxopf cannot use; cvxopf's own errors pass through
    """
    from cvxopf import build_opf, extract_results, ACCEPTED_SOLVER_STATUSES, \
        OPFOptions, NumericalPreparation # pylint: disable=import-outside-toplevel
    opts = solve_options(options,formulation)
    notes = []
    def note(text):
        notes.append(text)
        log.write(text + "\n")
    start = time.time()
    bus,gen,branch = ppc["bus"],ppc["gen"],ppc["branch"]
    live = bus[:,1] != 4
    ids = set(bus[live,0].astype(int))
    gkeep = np.array([int(b) in ids for b in gen[:,0]],dtype=bool)
    bkeep = np.array([int(f) in ids and int(t) in ids for f,t in branch[:,:2]],dtype=bool)
    gencost = ppc["gencost"]
    if len(gencost) == 2*len(gen):
        note("Reactive power cost rows are ignored: cvxopf has no reactive power costs")
        gencost = gencost[:len(gen)]
    case = {"version":"2","baseMVA":float(ppc["baseMVA"]),"bus":bus[live],
        "gen":gen[gkeep],"branch":branch[bkeep],"gencost":gencost[gkeep]}
    links,rows = [],[]
    if "dcline" in ppc:
        if formulation == "singlenode_dc":
            note("DC lines are ignored by the copper-plate model (singlenode_dc)")
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
        build = build_opf(case,formulation=formulation,hvdc=links or None,options=build_options)
        with _capture_output(log,opts["verbose"]):
            build.solve(**kwargs)
        res = extract_results(build)
        recovered = None
        if formulation == "socp" and res["status"] in ACCEPTED_SOLVER_STATUSES:
            from cvxopf import recover_socp_voltage # pylint: disable=import-outside-toplevel
            recovered = recover_socp_voltage(build,res)
    seen = set()
    for w in caught:
        text = str(w.message).strip()
        if issubclass(w.category,UserWarning) and text not in seen:
            seen.add(text)
            note(f"cvxopf warning: {text}")
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
        if exact is False:
            notes.append("SOCP relaxation is not exact: Vm and Va are not an AC power flow solution")
        elif feasible is False:
            notes.append("SOCP relaxation: the recovered voltages are not AC feasible")
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
