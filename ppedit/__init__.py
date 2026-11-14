"""Browser-based editor for PYPOWER and Arras Energy `module pypower` models

Syntax
------

    ppedit serve [FILE] [-g CSV] [-p PORT] [--host HOST] [--no-browser] [-d] [-w]
    ppedit convert INPUT OUTPUT [-g CSV] [-d] [-w]
    ppedit check FILE [-d] [-w]

Positional arguments
--------------------

  - `serve`: start the editor web server and open it in a browser

  - `convert`: convert a case between `.py`, `.json`, and `.glm`

  - `check`: print validation issues; exit code 1 if there are errors

  - `FILE`, `INPUT`, `OUTPUT`: case files; the extension selects the format

Optional arguments
------------------

  - `-d|--debug`: enable debug logging and traceback on exceptions

  - `-g|--geodata CSV`: add bus locations from a CSV file with at least the
    columns `BUS_I`, `LAT` and `LON` (other columns are kept as auxiliary
    data) to the case being served or converted

  - `-h|--help`: show this help message and exit

  - `-p|--port PORT`: server port (default 8050)

  - `--host HOST`: interface to listen on (default `127.0.0.1`)

  - `--no-browser`: do not open a browser window

  - `-w|--warning`: enable warning messages from python

Description
-----------

`ppedit` is a local web application for building and editing power system
models used by the PYPOWER solver, either as PYPOWER case files or as
Arras Energy GridLAB-D models that use `module pypower`. A Flask server
holds one case in memory and exposes it through a JSON API; the browser
page edits the bus, generator, branch, generator cost, DC line, DC line
cost and area tables, draws the network, checks the case, and runs PYPOWER power flow
and optimal power flow.

Given a case file, `ppedit.Case` is expected to satisfy:

  1. **Round trip.** `Case.load(path).dumps(fmt)` re-read with `Case.load`
     gives the same tables for `py`, `json`, and `glm`, except `dclinecost`
     and the price reference buses of `areas`, which GLM cannot hold (bus
     `area` numbers are kept, and `areas` rows are rebuilt from them).

  2. **Referential integrity.** Edits through `Case.set_value`,
     `Case.add_row`, and `Case.delete_rows` never leave a generator, branch
     or DC line, or an area's price reference bus, pointing to a bus that
     does not exist: renumbering a bus renumbers its references, and
     deleting a bus with connections (or that is a price reference bus) is
     refused unless `cascade=True`. Renumbering an area in the `areas`
     table renumbers the `area` of its buses.

  3. **Cost alignment.** When the case has `gencost` (`dclinecost`), adding
     or deleting a generator (DC line) adds or deletes its cost row(s).

  4. **No code execution.** `.py` case files are parsed, never imported or
     run, so a case file from elsewhere cannot run code on the server.

  5. **Geodata.** Bus locations (`Case.geodata`, rows of `[BUS_I, LAT, LON,
     aux...]` with auxiliary column names in `Case.geotags`) are read from a
     CSV file, from the `geodata` (and `geotags`) keys of a `.py`/`.json`
     case, or from GLM bus `latitude`/`longitude`, and are written back under
     the same keys. Renumbering a bus renumbers its geodata and deleting a
     bus deletes its geodata.

The *Claude* tab runs any number of Claude sessions (`ppedit.claude`) that
read and edit the case through the same checked, undoable edits as the
browser, optionally using web data or local files, when an Anthropic API
key is configured.

The network diagram has a *Geodata* layout, available when the case has
geodata, that places buses at their locations on a map (Web Mercator) with
optional USGS satellite imagery, Census TIGERweb roads, and state and county
boundaries and names. Buses without geodata are drawn next to their located
neighbors (dashed) or left out.

What is not promised
--------------------

  - One case per server, held in memory, for one user on the local machine.
    There is no authentication: do not expose the server on a shared network.

  - Only the solver-layer GLM objects (`bus`, `gen`, `branch`, `gencost`,
    `dcline`) are edited; other `module pypower` objects (`load`,
    `powerplant`, ...) and other modules are reported on load and dropped on
    save.

  - `dclinecost` and `areas` are not written to GLM (there are no
    GridLAB-D objects for them). `areas` is not passed to the solver: the
    per-area results (`Case.solve`) are sums over the bus `area` column, and
    an area's price is the OPF `lam_P` at its price reference bus.

  - DC lines are solved with ppedit's own implementation of the
    `pypower.toggle_dcline` model (that function fails with current numpy).
    As in PYPOWER, power flow does not enforce the DC line reactive power
    limits, and a DC line terminal bus becomes a PV bus held at the line's
    voltage setpoint (taking precedence over a generator's `Vg` at that
    bus). A bus connected only by DC lines is still isolated, so its DC
    lines are not solved.

  - The force-directed layout is not geography and its positions are not
    saved; bus positions are kept only as geodata, which the diagram does not
    edit (load a CSV or edit the case file). Map layers are fetched by the
    browser from public U.S. government map services (USGS The National Map,
    Census Bureau TIGERweb): they need network access, cover the United
    States only, and are not cached by `ppedit`. GLM output keeps bus
    locations but not auxiliary geodata. In the classic style a bus with both generation and load
    is drawn as a generator (circle); its label shows both MW values. The
    one-line style computes its own layered layout from the topology (it
    is not dragged); each line has a column at each of its buses joined by
    one horizontal segment, which keeps bus bars short but makes large
    meshed cases wide, and lines that skip rows still cross bus bars.

  - Claude sessions send the case data they read, and the files and web
    pages they use, to the Anthropic API; they are kept in server memory
    only and end when the server stops. Changes Claude makes are checked
    like any other edit but not reviewed: look at them (and undo them)
    before saving.

  - Buses with no in-service branch are solved as isolated (type NONE)
    without changing the case.

Data
----

Columns follow `pypower.caseformat`; units are listed in `ppedit.case.COLUMNS`.

```mermaid
flowchart LR
    file[(.py / .json / .glm)] --> Case
    csv[(geodata .csv)] --> Case
    maps[USGS / TIGERweb map services] --> browser
    Case <-->|JSON API| server[Flask server]
    server <--> browser[Browser editor]
    Case --> solver[PYPOWER runpf / runopf]
    server <-->|tools| claude[Claude sessions]
    claude <--> api[Anthropic API]
    web[(web data)] --> claude
    files[(local files)] --> claude
```
"""

try:
    import importlib.metadata
    __version__ = importlib.metadata.version("ppedit")
except Exception: # pylint: disable=broad-exception-caught
    __version__ = "dev"

from ppedit.case import Case, CaseError, COLUMNS, TABLES, builtin_cases
from ppedit.server import create_app
from ppedit.cli import main
