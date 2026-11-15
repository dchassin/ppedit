"""Build examples/wecc240_2018.py: the WECC 240 case with generator costs

Syntax: python3 examples/build_wecc240_2018.py

Reads `examples/wecc240_2011.py` (the case and its geodata), gives each
generator the polynomial cost (`c2,c1,c0`) of its type from
`examples/wecc240_gentypes.csv`, and writes `examples/wecc240_2018.py`
(case name `wecc240_2018`). A generator's type is the PSS/E generator ID at
the end of its `id` in `examples/wecc240_cost.csv` (`<bus name>_<ID>`, one
row per generator in case order); the bus name is checked against
`examples/wecc240_bus.csv`. Start-up and shutdown costs are 0.
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,os.path.dirname(HERE))
from ppedit import Case # pylint: disable=wrong-import-position

def read(name):
    """Rows of an examples CSV file"""
    with open(os.path.join(HERE,name),newline="",encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

def main():
    """Write examples/wecc240_2018.py"""
    case = Case.load(os.path.join(HERE,"wecc240_2011.py"))
    types = {r["type"]:[float(r[x]) for x in ("c2","c1","c0")] for r in read("wecc240_gentypes.csv")}
    names = {int(r["id"]):r["name"] for r in read("wecc240_bus.csv")}
    ids = [r["id"] for r in read("wecc240_cost.csv")]
    gens = case.tables["gen"]
    if len(ids) != len(gens):
        raise ValueError(f"wecc240_cost.csv has {len(ids)} rows for {len(gens)} generators")
    rows = []
    for n,(gen,gid) in enumerate(zip(gens,ids)):
        name,gtype = gid.rsplit("_",1)
        if names.get(gen[0]) != name:
            raise ValueError(f"generator {n+1} is at bus {gen[0]} ({names.get(gen[0])}), "
                f"not {name} as wecc240_cost.csv says")
        if gtype not in types:
            raise ValueError(f"generator {n+1} type {gtype} is not in wecc240_gentypes.csv")
        rows.append([2,0.0,0.0,3,",".join(f"{x:g}" for x in types[gtype])])
    case.tables["gencost"] = rows
    case.name = "wecc240_2018"
    errors = [x["message"] for x in case.validate() if x["level"] == "error"]
    if errors:
        raise ValueError("; ".join(errors))
    case.save(os.path.join(HERE,"wecc240_2018.py"))
    print(f"wrote examples/wecc240_2018.py: {case.size()}")

if __name__ == "__main__":
    main()
