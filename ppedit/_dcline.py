"""DC line model for the PYPOWER solver (private)

`add_dclines(ppc)` registers PYPOWER `userfcn` callbacks that model the
rows of `ppc["dcline"]` as in `pypower.toggle_dcline`: each in-service DC
line becomes a pair of dummy generators, one taking the flow out of the
"from" bus and one injecting it at the "to" bus, linked in OPF by the loss
constraint `PT = PF - (LOSS0 + LOSS1 * PF)`. Both terminal buses become PV
(the REF bus stays REF), so the line also holds the terminal voltages at
`VF` and `VT` and supplies reactive power within `QMINF..QMAXF` and
`QMINT..QMAXT` (enforced by OPF only, as for generators).

This is a reimplementation rather than a call to `toggle_dcline`, which
fails with current numpy (float array indices) and mishandles
`dclinecost`, out-of-service lines in OPF results, and `gencost` with
reactive power cost rows.

After the solve, `results["dcline"]` holds every row of the input
`dcline` (external bus numbers) with `PF`, `PT`, `QF`, `QT`, `VF` and `VT`
replaced by the solution for in-service lines, padded to 23 columns with
the OPF multipliers (`MU_PMIN` .. `MU_QMAXT`, zero for power flow).

References
----------

  - `pypower.toggle_dcline` and `pypower.idx_dcline`
  - R. D. Zimmerman, C. E. Murillo-Sanchez, MATPOWER User's Manual, section
    "DC Transmission Lines"
"""

import numpy as np

from pypower.add_userfcn import add_userfcn # pylint: disable=no-name-in-module
from pypower.toggle_dcline import userfcn_dcline_formulation # pylint: disable=no-name-in-module
from pypower.idx_bus import BUS_TYPE, REF, PV # pylint: disable=no-name-in-module
from pypower.idx_gen import GEN_BUS, PG, QG, QMAX, QMIN, VG, MBASE, \
    GEN_STATUS, PMAX, PMIN, MU_PMAX, MU_PMIN, MU_QMAX, MU_QMIN # pylint: disable=no-name-in-module
from pypower.idx_cost import MODEL, NCOST, POLYNOMIAL # pylint: disable=no-name-in-module

(F_BUS,T_BUS,BR_STATUS,PF,PT,QF,QT,VF,VT,PMIN_DC,PMAX_DC,QMINF,QMAXF,QMINT,
    QMAXT,LOSS0,LOSS1,MU_PMIN_DC,MU_PMAX_DC,MU_QMINF,MU_QMAXF,MU_QMINT,MU_QMAXT) = range(23)

NCOL = 23
"""Columns of a solved `dcline` matrix (17 input columns and 6 multipliers)"""

def add_dclines(ppc:dict) -> dict:
    """Register the DC line callbacks on a `ppc` dict that has a `dcline` matrix"""
    ppc = add_userfcn(ppc,"ext2int",_ext2int)
    ppc = add_userfcn(ppc,"formulation",userfcn_dcline_formulation)
    ppc = add_userfcn(ppc,"int2ext",_int2ext)
    return ppc

def flip_cost(row:np.ndarray) -> np.ndarray:
    """Return a cost row of PF as a cost row of the "from" dummy generator (PG = -PF)

    Polynomial coefficients of odd powers change sign; piecewise linear
    points are mirrored about zero and reversed so MW values increase.
    """
    row = np.array(row,dtype=float)
    n = int(row[NCOST])
    if row[MODEL] == POLYNOMIAL:
        coef = row[NCOST+1:NCOST+1+n]
        for j in range(n):
            if (n-1-j) % 2:
                coef[j] = -coef[j]
    else:
        pts = row[NCOST+1:NCOST+1+2*n].reshape(n,2)
        pts = np.column_stack([-pts[::-1,0],pts[::-1,1]])
        row[NCOST+1:NCOST+1+2*n] = pts.ravel()
    return row

def _pad(rows,width):
    rows = np.atleast_2d(np.asarray(rows,dtype=float))
    if rows.shape[1] < width:
        rows = np.hstack([rows,np.zeros((rows.shape[0],width-rows.shape[1]))])
    return rows

def _ext2int(ppc,*_):
    """Append the dummy generators (and their costs) in internal indexing"""
    ext = _pad(ppc["dcline"],NCOL)
    on = np.flatnonzero(ext[:,BR_STATUS] > 0)
    ppc["order"]["ext"]["dcline"] = ext
    ppc["order"]["ext"]["dcline_on"] = on
    dc = ext[on,:].copy()
    ndc = len(on)
    e2i = ppc["order"]["bus"]["e2i"]
    fbus = e2i[dc[:,F_BUS].astype(int)].astype(int)
    tbus = e2i[dc[:,T_BUS].astype(int)].astype(int)
    dc[:,F_BUS],dc[:,T_BUS] = fbus,tbus
    # consistent starting flows for power flow
    dc[:,PT] = dc[:,PF] - (dc[:,LOSS0] + dc[:,LOSS1]*dc[:,PF])
    ppc["dcline"] = dc

    gen = ppc["gen"]
    ng = gen.shape[0]
    fg = np.zeros((ndc,gen.shape[1]))
    fg[:,MBASE] = 100
    fg[:,GEN_STATUS] = 1
    fg[:,PMIN],fg[:,PMAX] = -np.inf,np.inf
    tg = fg.copy()
    fg[:,GEN_BUS],tg[:,GEN_BUS] = fbus,tbus
    fg[:,PG],tg[:,PG] = -dc[:,PF],dc[:,PT]
    fg[:,QG],tg[:,QG] = dc[:,QF],dc[:,QT]
    fg[:,VG],tg[:,VG] = dc[:,VF],dc[:,VT]
    k = dc[:,PMIN_DC] >= 0
    fg[k,PMAX] = -dc[k,PMIN_DC]
    k = dc[:,PMAX_DC] >= 0
    fg[k,PMIN] = -dc[k,PMAX_DC]
    k = dc[:,PMIN_DC] < 0
    tg[k,PMIN] = dc[k,PMIN_DC]
    k = dc[:,PMAX_DC] < 0
    tg[k,PMAX] = dc[k,PMAX_DC]
    fg[:,QMIN],fg[:,QMAX] = dc[:,QMINF],dc[:,QMAXF]
    tg[:,QMIN],tg[:,QMAX] = dc[:,QMINT],dc[:,QMAXT]
    # keep dummy gens from being read as dispatchable loads (PMIN < 0 = PMAX)
    for g in (fg,tg):
        g[(g[:,PMIN] < 0) & (g[:,PMAX] == 0),PMAX] = -1e-6

    bus = ppc["bus"]
    ref = bus[:,BUS_TYPE] == REF
    bus[fbus,BUS_TYPE] = PV
    bus[tbus,BUS_TYPE] = PV
    bus[ref,BUS_TYPE] = REF
    ppc["gen"] = np.vstack([gen,fg,tg])

    gencost = ppc.get("gencost")
    if gencost is not None and len(gencost):
        zero = np.array([[POLYNOMIAL,0,0,2,0,0]])
        if "dclinecost" in ppc and len(ppc["dclinecost"]):
            dcc = _pad(ppc["dclinecost"],6)[on,:]
            fcost = np.vstack([flip_cost(r) for r in dcc]) if ndc else np.zeros((0,6))
        else:
            fcost = np.repeat(zero,ndc,axis=0)
        tcost = np.repeat(zero,ndc,axis=0)
        width = max(gencost.shape[1],fcost.shape[1] if ndc else 0,6)
        gencost,fcost,tcost = _pad(gencost,width),_pad(fcost,width),_pad(tcost,width)
        if gencost.shape[0] == 2*ng: # reactive power costs follow the real power costs
            qcost = np.repeat(_pad(zero,width),2*ndc,axis=0)
            gencost = np.vstack([gencost[:ng],fcost,tcost,gencost[ng:],qcost])
        else:
            gencost = np.vstack([gencost,fcost,tcost])
        ppc["gencost"] = gencost
    return ppc

def _int2ext(results,*_):
    """Remove the dummy generators and put the DC line solution in `dcline`"""
    order = results["order"]
    ext = order["ext"]["dcline"].copy()
    on = order["ext"]["dcline_on"]
    ndc = len(on)
    gen = results["gen"]
    ng = gen.shape[0] - 2*ndc
    fg,tg = gen[ng:ng+ndc,:],gen[ng+ndc:,:]
    ext[on,PF] = -fg[:,PG]
    ext[on,PT] = tg[:,PG]
    ext[on,QF] = fg[:,QG]
    ext[on,QT] = tg[:,QG]
    ext[on,VF] = fg[:,VG]
    ext[on,VT] = tg[:,VG]
    off = np.setdiff1d(np.arange(ext.shape[0]),on)
    ext[np.ix_(off,[PF,PT,QF,QT])] = 0
    ext[:,MU_PMIN_DC:] = 0
    if gen.shape[1] > MU_QMIN:
        ext[on,MU_PMIN_DC] = fg[:,MU_PMAX] + tg[:,MU_PMIN]
        ext[on,MU_PMAX_DC] = fg[:,MU_PMIN] + tg[:,MU_PMAX]
        ext[on,MU_QMINF] = fg[:,MU_QMIN]
        ext[on,MU_QMAXF] = fg[:,MU_QMAX]
        ext[on,MU_QMINT] = tg[:,MU_QMIN]
        ext[on,MU_QMAXT] = tg[:,MU_QMAX]
    results["gen"] = gen[:ng,:] # int2ext restores gencost from its external copy
    results["dcline"] = ext
    return results
