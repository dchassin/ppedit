"""Build the WECC 240 generator Pmax panel from the eudoxys/generation node data

Reads the WECC 240 node files of `eudoxys/generation` (`data/wecc`:
`node_capacity_{category}.csv.gz` and `node_actual_{category}.csv.gz`, hourly
from 2018 to 2022, one column per generation node geohash) and writes a
panel of generator `Pmax` by hour for `wecc240_2018.py` (the same generator
rows as `wecc240_2011.py` and `wecc240_map.py`):

    timestamp,1,2,...
    2018-01-01 00:00:00+00:00,...

one column per generator row (counted from 1), to be read on the Series tab
with *Import ▾ › Panel…* (table gen, value Pmax, columns are row numbers).

Method
------

1. Node `Pmax(t)` is the operating capacity of the dispatchable categories
   (`generation.fuels.DISPATCHABLE`: all but solar and wind, storage
   included) plus the actual net generation of the non-dispatchable ones
   (solar and wind), negative hours set to 0.
2. Each generator is placed at the node whose geohash is that of its bus
   (6 digits, from the case geodata). A generator at a US bus (one with a
   county FIPS code) that is not at a node goes to the node that received
   its county's generation in the node data: the `gen` column of wecc240
   `county_node_map.csv`, the county named from its FIPS code by wecc240
   `counties.csv` (the nearest node if the county is not in the map).
   Generators outside the US (no county) get no column and keep their case
   `Pmax`.
3. A node's `Pmax(t)` is shared among its generators in proportion to
   their case `Pmax` (equally if those are all 0).

Syntax
------

    python3 examples/build_wecc240_generation.py DATA [--wecc240 FOLDER]
        [--case CASE] [--output CSV]

`DATA` is the `data/wecc` folder of a checkout of eudoxys/generation (a
sparse checkout of that folder is enough). `--wecc240` is the
`wecc240/data` folder of eudoxys/wecc240 holding `counties.csv` and
`county_node_map.csv` (default: read from GitHub).
"""

import argparse
import math
import os
import sys

import pandas as pd

sys.path.insert(0,os.path.join(os.path.dirname(__file__),".."))
from ppedit import Case # pylint: disable=wrong-import-position
from ppedit.case import geohash # pylint: disable=wrong-import-position

WECC240 = "https://raw.githubusercontent.com/eudoxys/wecc240/main/wecc240/data"

DISPATCHABLE = ["coal","natural_gas","oil","nuclear","hydro","geothermal","biomass",
    "battery_storage","pumped_storage","other"]
NONDISPATCHABLE = ["solar","wind"]

def _read(path):
    return pd.read_csv(path,index_col="timestamp")

def _distance(a,b):
    """Great-circle distance (km) between two (lat, lon) points"""
    la1,lo1,la2,lo2 = map(math.radians,(*a,*b))
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*6371*math.asin(math.sqrt(h))

def _decode(code):
    """Center (lat, lon) of a geohash cell"""
    base = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat,lon,even = [-90.0,90.0],[-180.0,180.0],True
    for ch in code:
        bits = base.index(ch)
        for k in range(4,-1,-1):
            box = lon if even else lat
            mid = (box[0]+box[1])/2
            box[0 if bits >> k & 1 else 1] = mid
            even = not even
    return (lat[0]+lat[1])/2,(lon[0]+lon[1])/2

def build(data,case_path,output,wecc240=WECC240,verbose=True):
    """Build the panel; return the report lines"""
    report = []
    sep = "/" if "://" in wecc240 else os.sep
    counties = pd.read_csv(wecc240+sep+"counties.csv",dtype={"FIPS":str})
    county_st = {int(f):n for f,n in zip(counties["FIPS"],counties["COUNTY_ST"])}
    mapping = pd.read_csv(wecc240+sep+"county_node_map.csv")
    county_node = dict(zip(mapping["county_st"],mapping["gen"]))
    cap = sum(_read(os.path.join(data,f"node_capacity_{x}.csv.gz")) for x in DISPATCHABLE)
    act = [_read(os.path.join(data,f"node_actual_{x}.csv.gz")) for x in NONDISPATCHABLE]
    negative = int(sum((a < 0).to_numpy().sum() for a in act))
    act = sum(a.clip(lower=0) for a in act)
    node = cap + act
    report.append(f"{len(node)} hours, {len(node.columns)} nodes; {negative} negative solar/wind "
        "node-hours set to 0")

    case = Case.load(case_path)
    tags = case.geotags
    county = tags.index("COUNTY") + 3 if "COUNTY" in tags else None
    where = {}
    for row in case.geodata:
        where.setdefault(row[0],row)
    centers = {n:_decode(n) for n in node.columns}
    members = {n:[] for n in node.columns}
    for k,gen in enumerate(case.tables["gen"]):
        geo = where.get(gen[0])
        if geo is None:
            report.append(f"gen {k+1} at bus {gen[0]}: no geodata, keeps its case Pmax")
            continue
        code = geohash(geo[1],geo[2],6)
        if code in members:
            members[code].append(k)
            continue
        if county is None or geo[county] in ("",None):
            report.append(f"gen {k+1} at bus {gen[0]}: outside the US, keeps its case Pmax {gen[8]:g} MW")
            continue
        name = county_st.get(int(geo[county]))
        near = county_node.get(name)
        how = f"{name} node"
        if near not in members:
            near = min(centers,key=lambda n: _distance((geo[1],geo[2]),centers[n]))
            how = f"nearest node ({name or 'county ' + str(geo[county])} not mapped)"
        members[near].append(k)
        report.append(f"gen {k+1} at bus {gen[0]} ({code}): {how} {near}, "
            f"{_distance((geo[1],geo[2]),centers[near]):.0f} km")

    columns = {}
    for n,gens in members.items():
        if not gens:
            report.append(f"node {n}: no generator, {node[n].max():.0f} MW peak not used")
            continue
        base = [case.tables["gen"][k][8] for k in gens]
        total = sum(base)
        for k,b in zip(gens,base):
            share = b/total if total > 0 else 1/len(gens)
            columns[k+1] = (node[n]*share).round(3)
    panel = pd.DataFrame({str(k):columns[k] for k in sorted(columns)},index=node.index)
    panel.index.name = "timestamp"
    pmin = {k:case.tables["gen"][k-1][9] for k in columns}
    low = sum(int((panel[str(k)] < pmin[k]).sum()) for k in columns)
    if low:
        report.append(f"{low} generator-hours have Pmax below the case Pmin")
    panel.to_csv(output,compression="gzip")
    report.append(f"wrote {output}: {len(panel)} rows, {len(panel.columns)} generators "
        f"(of {len(case.tables['gen'])})")
    if verbose:
        print("\n".join(report))
    return report

def main(argv=None):
    """Command line entry point"""
    parser = argparse.ArgumentParser(description=__doc__.split("\n",1)[0])
    parser.add_argument("data",help="data/wecc folder of eudoxys/generation")
    parser.add_argument("--wecc240",default=WECC240,help="wecc240/data folder of eudoxys/wecc240")
    here = os.path.dirname(os.path.abspath(__file__))
    parser.add_argument("--case",default=os.path.join(here,"wecc240_2018.py"))
    parser.add_argument("--output",default=os.path.join(here,"wecc240_generation.csv.gz"))
    args = parser.parse_args(argv)
    build(args.data,args.case,args.output,args.wecc240)

if __name__ == "__main__":
    main()
