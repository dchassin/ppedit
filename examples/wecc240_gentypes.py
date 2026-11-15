"""Derive WECC 240 generator type cost curves from NREL's scheduling data

Syntax: python3 examples/wecc240_gentypes.py XLSX [CSV]

  - `XLSX`: NREL's `WECC240_2018_Generation_scheduling.xlsx` (in
    https://github.com/eudoxys/wecc240, `wecc240/data/`)
  - `CSV`: generator type table to rewrite (default
    `examples/wecc240_gentypes.csv`, columns `type,c2,c1,c0`)

The PSS/E generator ID of each unit in `examples/wecc240_case.raw` is its
type (`C`, `G`, `NG`, ...). NREL's `Generator` sheet names the same units
`<bus><ID>` (e.g. `1032G`) and gives each a `Gen_Type`, a start-up and a
no-load cost, and a marginal cost curve of up to four segments (`CostN`
$/MWh from the previous `MWN`, starting at `Pmin`, to `MWN`).

For each type the script writes PYPOWER polynomial costs `c2*P^2 + c1*P + c0`
($/h, `P` in MW):

  - `c1` is the first segment marginal cost of the type's units, weighted by
    `Pmax`;
  - `c2` makes each unit's variable cost from `Pmin` to `Pmax` equal to its
    curve, scaled to the type's median unit size (one row serves units of
    every size, so `c2` is too steep for units much larger than the median
    and too flat for smaller ones);
  - `c0` is 0: no-load costs matter only for unit commitment, and one `c0`
    in $/h cannot fit units of different sizes. They are printed per MW of
    capacity for reference.

Zero-priced segments after a priced one (units whose `Pmax` was raised for
2018 past the end of their curve) take the last price. Types with no NREL
unit (0 MW in the case) and types NREL prices at 0 (hydro, wind, solar,
geothermal, biomass, one nuclear unit) are written as 0.

Output: the rewritten CSV, and a table of the fit on stdout. Needs
`openpyxl` (`pip install openpyxl`; not a `ppedit` dependency).
"""

import collections
import csv
import re
import statistics
import sys

import openpyxl

def unit_fit(unit):
    """Return (c1, k) for one unit, `k = c2*Pmax`, or `None` without a curve"""
    lo = pmin = unit["Pmin"] or 0.0
    last,cost,first = 0.0,0.0,None
    for k in range(1,5):
        hi,mc = unit[f"MW{k}"],unit[f"Cost{k}"]
        if mc == 0 and last > 0:
            mc = last   # zero-priced tail where Pmax was raised past the curve
        last = mc
        if hi > lo + 1e-6:
            first = mc if first is None else first
            cost += mc*(hi-lo)
            lo = hi
    if first is None:
        return None
    span2 = unit["Pmax"]**2 - pmin**2
    c2 = (cost - first*(lo-pmin))/span2 if span2 > 0 else 0.0
    return first,c2*unit["Pmax"]

def main(argv):
    """Rewrite the generator type table from the NREL workbook"""
    if not 1 <= len(argv) <= 2:
        print(__doc__.split("\n\n",2)[1],file=sys.stderr)
        return 1
    xlsx = argv[0]
    path = argv[1] if len(argv) > 1 else "examples/wecc240_gentypes.csv"
    book = openpyxl.load_workbook(xlsx,read_only=True,data_only=True)
    rows = list(book["Generator"].iter_rows(values_only=True))
    units = [dict(zip(rows[0],r)) for r in rows[1:] if r[0] is not None]
    bytype = collections.defaultdict(list)
    for unit in units:
        bytype[re.sub(r"^\d+","",str(unit["genname"]))].append(unit)
    with open(path,newline="",encoding="utf-8") as fh:
        types = [r["type"] for r in csv.DictReader(fh)]
    out = []
    for code in types:
        group = bytype.get(code,[])
        fits = [(unit_fit(u),u["Pmax"]) for u in group]
        fits = [(f,w) for f,w in fits if f is not None]
        weight = sum(w for _,w in fits)
        c1 = sum(f[0]*w for f,w in fits)/weight if weight else 0.0
        k = sum(f[1]*w for f,w in fits)/weight if weight else 0.0
        size = statistics.median(u["Pmax"] for u in group) if group else 0.0
        c2 = k/size if size else 0.0
        capacity = sum(u["Pmax"] for u in group)
        noload = sum(u["No_Load_Cost"] for u in group)/capacity if capacity else 0.0
        kinds = "/".join(sorted({u["Gen_Type"] for u in group})) or "(no NREL unit)"
        out.append((code,c2,c1,0.0,kinds,len(group),size,noload))
    with open(path,"w",newline="",encoding="utf-8") as fh:
        writer = csv.writer(fh,lineterminator="\n")
        writer.writerow(["type","c2","c1","c0"])
        for code,c2,c1,c0,*_ in out:
            writer.writerow([code,f"{c2:.4g}",f"{c1:.4g}",f"{c0:g}"])
    print("type  c2 $/MW2h  c1 $/MWh  NREL type    units  median MW  no-load $/MWh")
    for code,c2,c1,_,kinds,n,size,noload in out:
        print(f"{code:4s}  {c2:9.4g}  {c1:8.4g}  {kinds:11s}  {n:5d}  {size:9.0f}  {noload:13.1f}")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
