The WECC 240 data was developed from the original WECC 240 PSS/E file
`wecc240_case.raw` originally created by CAISO and redistributed by NLR. This
represents the original 2011 WECC system model. 

The file `wecc240_case.py` is
updated with the `wecc240_*.csv` files from NLR and represents the system as of 2018. 

The GLM and JSON files currently represent the WECC system using the same model as the PY file (2018 system) but will eventually represent the 2020 system from NLR when it becomes available.

`wecc240_gentypes.csv` gives PYPOWER polynomial costs (`c2*P^2 + c1*P +
c0`, $/h with `P` in MW) for each generator type, the PSS/E generator ID
in `wecc240_case.raw` (`C` coal steam, `E` steam, `G` gas, `H` hydro, `S`
solar, `W` wind, `N` nuclear, `B` biomass; two-letter IDs are units at
California nodes, e.g. `NG`, `NH`, `NN` for Diablo Canyon). It is derived
from the unit cost curves in NREL's `WECC240_2018_Generation_scheduling.xlsx`
(https://github.com/eudoxys/wecc240, `wecc240/data/`) by

    python3 examples/wecc240_gentypes.py WECC240_2018_Generation_scheduling.xlsx

`c1` is the first-segment marginal cost of the type's units; `c2` matches
their variable cost at full output for a unit of the type's median size;
`c0` is 0 (no-load costs are for unit commitment). NREL prices hydro,
wind, solar, geothermal, biomass and the Diablo Canyon unit at 0, and the
case's `Pmax` for them is nameplate capacity, so a single-snapshot OPF
runs them at `Pmax` unless their `Pmax` is first limited to what is
available. `SC` and `ND` (0 MW units) have no NREL curve.

`wecc240_cost.csv` has one polynomial cost row per generator (`id` is the
bus name and PSS/E generator ID), with the `c2,c1,c0` of its type from
`wecc240_gentypes.csv`; start-up and shutdown costs are 0.

In `wecc240_2011.py` the Pacific DC Intertie (CELILO 4010 to SYLMARLA 2619)
and the Intermountain line (INTERMT 2604 to ADELANTO 2600) are DC lines with
zero `dclinecost`. The PSS/E case models them as a load at the rectifier
bus and a negative load at the inverter bus. The DC lines take the same
power at the rectifier (`Pf` 3133.1 and 1845.7 MW) with 3.5% losses
(`loss1` 0.035; `Pt` 3023.4 and 1781.1 MW); the rest of the difference is
load at the inverter bus (526.0 MW at SYLMARLA, 100.3 MW at ADELANTO), so
the power flow solution is unchanged. `Pmax` is `Pf`, there is no reactive
power, and `Vf`, `Vt` are the case voltages.

`wecc240_2018.py` is `wecc240_2011.py` (with its geodata) plus one
polynomial cost row per generator, the costs of its type from
`wecc240_gentypes.csv`, and each branch `rateA` (0 in the source case) set
to its first nonzero `rateB` or `rateC` (0, unlimited, if both are 0).
Bus voltages are then kept at or below 1.087 pu (under the 1.1 pu `Vmax`):
the generators at buses 4039, 4131, 4132, 4231 and 6433 get `Vg` 1.08 pu,
and 1400 MVAr of shunt reactors (`Bs`) are added at buses 2901 (300), 2902
(200), 4002 (100), 4003 (200), 4090 (50), 4091 (50), 4094 (250) and 4097
(250). Last, the 13 rated branches loaded above 80% in that power flow get
`rateA` raised so they are loaded at 80% (a 20% margin), with `rateB` and
`rateC` raised to match where lower (issue #58). Rebuild it after
changing the costs with

    python3 examples/build_wecc240_2018.py

In `wecc240_2020.py` the areas are the balancing authorities of EIA Form 930
(issue #90), replacing the four NREL areas. `wecc240_ba.csv` gives the
EIA-930 code of each bus (`BUS_I,NAME,BA,NOTE`, the note saying why), and

    python3 examples/wecc240_areas.py

numbers the areas in alphabetical order of the codes, rebuilds the `areas`
rows with their default price reference buses and the code as the area
`name` (issue #93), sets the geodata `BA` column
to the code, and saves `wecc240_2020.py` in place:

| area | BA | name | buses |
|---:|---|---|---:|
| 1 | AESO | Alberta Electric System Operator | 2 |
| 2 | AZPS | Arizona Public Service | 7 |
| 3 | BANC | Balancing Authority of Northern California | 8 |
| 4 | BCHA | BC Hydro | 4 |
| 5 | BPAT | Bonneville Power Administration | 31 |
| 6 | CEN | CENACE (Baja California) | 2 |
| 7 | CISO | California ISO | 106 |
| 8 | IID | Imperial Irrigation District | 2 |
| 9 | IPCO | Idaho Power | 5 |
| 10 | LDWP | Los Angeles Department of Water and Power | 26 |
| 11 | NEVP | Nevada Power (NV Energy) | 8 |
| 12 | NWMT | NorthWestern Energy | 6 |
| 13 | PACE | PacifiCorp East | 16 |
| 14 | PACW | PacifiCorp West | 1 |
| 15 | PNM | Public Service Company of New Mexico | 4 |
| 16 | PSCO | Public Service Company of Colorado | 2 |
| 17 | PSEI | Puget Sound Energy | 2 |
| 18 | SRP | Salt River Project | 4 |
| 19 | SWPW | SPP RTO West (WACM until April 2026) | 4 |
| 20 | WALC | WAPA Desert Southwest | 3 |

A bus is assigned to the balancing authority of the substation or plant it
is named for (by voltage where a station is shared: Four Corners 500 kV is
AZPS and 345/230 kV PNM; Garrison 500 kV is BPAT and 230 kV NWMT; Sylmar
and Cottonwood have a bus per owner). The case's aggregate load buses take
the balancing authority of the station they are placed at, so an area's load
is not that balancing authority's EIA-930 demand: for example, the
northern Washington load at NORTH (PSEI) also serves SCL and TPWR, and BPAT
includes the Portland and Willamette Valley loads of PGE and PACW at
WILLAMET and WCASCADE. Balancing authorities with no bus of their own in the
240-bus case (AVA, CHPD, DOPD, EPE, GCPD, PGE, SCL, TEPC, TIDC, TPWR and the
generation-only ones) have no area. The other example files still have the
NREL areas, including `wecc240_2018.py`.

`wecc240_generation.csv.gz` is a panel of generator `Pmax` by hour, 2018 to
2022 (UTC, 43,824 hours), for the generator rows of `wecc240_2018.py` (the
same rows as `wecc240_2011.py` and `wecc240_map.py`): a `timestamp` column
and one column per generator row counted from 1. Read it on the Series tab
with *Import ▾ › Panel…* (table gen, value Pmax, columns are row numbers)
and a *From*/*To* range of at most 8,784 hours (a series holds at most one
leap year), e.g. 2020-01-01 to 2020-12-31. It is built from the WECC 240
node files of eudoxys/generation (`data/wecc`) by

    python3 examples/build_wecc240_generation.py GENERATION/data/wecc [--wecc240 WECC240/wecc240/data]

Each node's `Pmax` is the operating capacity of the dispatchable categories
(all but solar and wind, storage included) plus the actual solar and wind
net generation, shared among the node's generators in proportion to their
case `Pmax`. A generator is at the node with its bus's geohash; a US
generator elsewhere goes to the node its county's generation went to
(wecc240 `county_node_map.csv`), which places 13 California generators
(Los Angeles, Imperial, San Bernardino and San Diego counties). The 9
generators in Mexico and British Columbia have no column and keep their
case `Pmax`.

The node data places generation by county, not where the 240-bus model
aggregates it (e.g. 9.9 GW at H ALLEN in the case against 0.1 GW in its
node's data, 0.5 GW at OLIVE against 13.5 GW), so the generation moves
across the network and, with the branch ratings of `wecc240_2018.py`, a
multistep DC OPF (lossy network flow) of 1 July 2020 is infeasible; it
solves with the copper-plate model or without branch ratings.
