"""Set the areas of a WECC 240 case to the EIA-930 balancing authorities

Syntax: python3 examples/wecc240_areas.py [CASE]

Puts each bus of `CASE` (default `examples/wecc240_2020.py`) in the area of
its EIA Form 930 balancing authority code in `examples/wecc240_ba.csv` (one
row per bus, `BUS_I,NAME,BA,NOTE`; the name is checked against the case
geodata) and saves the case in place (issue #90). Areas are numbered 1, 2,
... in alphabetical order of the codes present, the `areas` rows are rebuilt
with their default price reference buses (`Case._default_areas`) and the
code as the area `name` (issue #93), and the geodata `BA` column is set to
the code. Nothing else in the case changes;
areas are not used by the solver.
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,os.path.dirname(HERE))
from ppedit import Case # pylint: disable=wrong-import-position


def balancing_authorities(case):
    """Put each bus of `case` in the area of its EIA-930 balancing authority

    Returns the area numbers by balancing authority code.
    """
    names = {row[0]:row[3+case.geotags.index("NAME")] for row in case.geodata}
    bas = {}
    with open(os.path.join(HERE,"wecc240_ba.csv"),newline="",encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            bus = int(r["BUS_I"])
            if names.get(bus) != r["NAME"]:
                raise ValueError(f"wecc240_ba.csv bus {bus} is {r['NAME']}, "
                    f"not {names.get(bus)} as the geodata says")
            bas[bus] = r["BA"]
    missing = [r[0] for r in case.tables["bus"] if r[0] not in bas]
    if missing:
        raise ValueError(f"wecc240_ba.csv has no row for buses {missing}")
    areas = {ba:n+1 for n,ba in enumerate(sorted(set(bas.values())))}
    for bus in case.tables["bus"]:
        bus[6] = areas[bas[bus[0]]]
    case.tables["areas"] = case._default_areas() # pylint: disable=protected-access
    codes = {n:ba for ba,n in areas.items()}
    for row in case.tables["areas"]:
        row[2] = codes[row[0]]
    col = 3 + case.geotags.index("BA")
    for row in case.geodata:
        row[col] = bas[row[0]]
    return areas

def main(path=os.path.join(HERE,"wecc240_2020.py")):
    """Set the areas of the case at `path` and save it in place"""
    case = Case.load(path)
    areas = balancing_authorities(case)
    errors = [x["message"] for x in case.validate() if x["level"] == "error"]
    if errors:
        raise ValueError("; ".join(errors))
    case.save(path)
    print(f"wrote {os.path.relpath(path)}: {len(areas)} areas: "
        f"{', '.join(f'{n} {ba}' for ba,n in areas.items())}")

if __name__ == "__main__":
    main(*sys.argv[1:2])
