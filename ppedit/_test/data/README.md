# Test data

| File | Purpose |
|---|---|
| `case9_geodata.csv` | Synthetic bus locations for the PYPOWER `case9` buses 1–9, written by hand (2026-11-13) at the coordinates of nine Northern California cities (illustrative, not the real network). Columns `BUS_I`, `LAT`, `LON` (decimal degrees) and auxiliary `NAME` (text) and `KV` (number). |
| `psse_v33.raw` | Hand-written 4-bus PSS/E revision 33 case (2026-11-15): in- and out-of-service loads (with constant current and admittance parts), fixed shunts, switched shunts and branches; branch end shunts; a negative (metered) `J`; a 2-winding transformer with `CW=2`, `CZ=2`, its own MVA base and `NOMV=0`; a 3-winding transformer with winding 3 out (`STAT=3`); a two-terminal DC line; bus 4 without voltage limits; zone and owner records (not converted). The expected values are worked out in `test_psse.py`. |
| `psse_v35.raw` | Hand-written 3-bus PSS/E revision 35 case (2026-11-15): `@!` field header lines, system-wide data, generator `NREG`, branch name and 12 ratings, one closed and one open system switching device, a switched shunt with `ID`. |
| `IEEE_14_bus.raw`, `IEEE_24_bus.raw`, `IEEE_30_bus.raw`, `IEEE_39_bus.raw`, `IEEE_57_bus.raw`, `IEEE_118_bus.raw`, `IEEE_300_bus.raw` | IEEE test systems as PSS/E revision 33 cases with solved voltages, added by the maintainer (2026-11-15); the first line of each file gives the date it was written (IEEE 24 by PowerWorld Simulator 19). They are not the same data as the PYPOWER built-in cases of the same names. Cases 14, 39 and 57 solve in PYPOWER to their stored voltages; the others have generators held at reactive power limits in their stored solution, which PYPOWER power flow does not enforce. |
| `case3.glm` | Hand-written 3-bus `module pypower` model (2026-11-13). Mixes numeric and keyword enumerations, `bus`/`parent`/`from`/`to`/`fbus`/`tbus` references, missing properties (defaults apply), inline comments, a nested `load` object, a `powerplant` object, `gencost` objects listed out of generator order, and a `dcline` object in the `gridlabd convert` style (unquoted names, numeric status, `Sfrom` = `Pf + j Pt`, an output property). |

Expected content after `Case.load`: 3 buses (REF, PV, PQ; 100 MW total
demand), 2 generators on buses 1 and 2, 3 branches, 2 polynomial cost rows
ordered gen 1 (`0.01,20,0`) then gen 2 (`0.02,25,0`), one DC line from bus
1 to bus 3 (`Pf` 10 MW, `Pt` 9.8 MW, `Pmax` 50 MW, `loss1` 0.02), and one
note listing the `load` and `powerplant` objects that are not kept.

# Source

IEEE test files downloaded from https://www.3phaseee.com/raw_files.