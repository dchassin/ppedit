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
to its first nonzero `rateB` or `rateC` (0, unlimited, if both are 0); rebuild it after
changing the costs with

    python3 examples/build_wecc240_2018.py
