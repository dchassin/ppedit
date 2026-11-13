# Test data

| File | Purpose |
|---|---|
| `case3.glm` | Hand-written 3-bus `module pypower` model (2026-11-13). Mixes numeric and keyword enumerations, `bus`/`parent`/`from`/`to`/`fbus`/`tbus` references, missing properties (defaults apply), inline comments, a nested `load` object, a `powerplant` object, and `gencost` objects listed out of generator order. |

Expected content after `Case.load`: 3 buses (REF, PV, PQ; 100 MW total
demand), 2 generators on buses 1 and 2, 3 branches, 2 polynomial cost rows
ordered gen 1 (`0.01,20,0`) then gen 2 (`0.02,25,0`), and one note listing
the `load` and `powerplant` objects that are not kept.
