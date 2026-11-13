# Test data

| File | Purpose |
|---|---|
| `case9_geodata.csv` | Synthetic bus locations for the PYPOWER `case9` buses 1–9, written by hand (2026-11-13) at the coordinates of nine Northern California cities (illustrative, not the real network). Columns `BUS_I`, `LAT`, `LON` (decimal degrees) and auxiliary `NAME` (text) and `KV` (number). |
| `case3.glm` | Hand-written 3-bus `module pypower` model (2026-11-13). Mixes numeric and keyword enumerations, `bus`/`parent`/`from`/`to`/`fbus`/`tbus` references, missing properties (defaults apply), inline comments, a nested `load` object, a `powerplant` object, `gencost` objects listed out of generator order, and a `dcline` object in the `gridlabd convert` style (unquoted names, numeric status, `Sfrom` = `Pf + j Pt`, an output property). |

Expected content after `Case.load`: 3 buses (REF, PV, PQ; 100 MW total
demand), 2 generators on buses 1 and 2, 3 branches, 2 polynomial cost rows
ordered gen 1 (`0.01,20,0`) then gen 2 (`0.02,25,0`), one DC line from bus
1 to bus 3 (`Pf` 10 MW, `Pt` 9.8 MW, `Pmax` 50 MW, `loss1` 0.02), and one
note listing the `load` and `powerplant` objects that are not kept.
