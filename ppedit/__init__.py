"""Browser-based editor for PYPOWER and Arras Energy `module pypower` models

Syntax
------

    ppedit serve [FILE] [-g CSV] [-p PORT] [--host HOST] [--no-browser] [-d] [-v] [-w]
    ppedit convert INPUT OUTPUT [-g CSV] [-d] [-v] [-w]
    ppedit check FILE [-d] [-v] [-w]
    ppedit --version

Positional arguments
--------------------

  - `serve`: start the editor web server and open it in a browser

  - `convert`: convert a case between `.py`, `.json`, and `.glm`, or from a
    PSS/E `.raw` case (read only) to one of them

  - `check`: print validation issues; exit code 1 if there are errors

  - `FILE`, `INPUT`, `OUTPUT`: case files; the extension selects the format
    (`INPUT` and `FILE` may also be PSS/E `.raw`, revisions 23 to 35)

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

  - `-v|--verbose`: enable `INFO:` messages on stderr (files loaded and
    saved, and with `serve` each HTTP request; request lines are shown
    only with this option, not with `-d` alone)

  - `--version`: print `ppedit` and the installed version, then exit

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
     gives the same tables for `py`, `json`, and `glm`, except the price
     reference buses and names of `areas`, which GLM cannot hold (bus
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
     case, or from GLM bus `latitude`/`longitude` and `pp_geodata` objects
     (a runtime class holding the auxiliary values, parent the bus), and are
     written back the same way. Renumbering a bus renumbers its geodata and deleting a
     bus deletes its geodata.

The *Claude* tab runs any number of Claude sessions (`ppedit.claude`) that
read and edit the case through the same checked, undoable edits as the
browser, optionally using web data or local files, when an Anthropic API
key is configured.

*Open ▾* opens a case file chosen on the browser's computer (*Local file…*)
or downloaded from an `http`/`https` URL typed in the menu (*From URL*). The
ppedit server downloads the URL (`ppedit.server.download_case`, at most
`ppedit.server.DOWNLOAD_LIMIT` bytes), so the server machine needs network
access to that host. A GitHub file page link (`.../blob/...`) is changed
to its raw file link, and other web pages are refused. The extension of the URL path selects the format, and
`.py` cases are parsed, never executed. Neither kind has a file path until
it is saved with *Save as*, or a folder is chosen in the top bar.

The top bar shows the folder of the open file (the full path is its
tooltip). Clicking it opens a folder chooser that browses folders on the
server machine (`POST /api/folders`, `ppedit.server.list_folder`); *Use
this folder* moves the file path into the chosen folder (`POST
/api/folder`) without writing anything, keeping the file name (or the case
name with `.glm` when there is no path). *Save* then writes it there.

The page takes optional URL parameters, so another tool can open it ready to
show (pptools `pprun` does): `?solve=pf` or `?solve=opf` runs that solver
when the page loads (`&algorithm=NAME` picks a PF algorithm or OPF solver as
in `POST /api/solve`, else the default), and `?view=NAME` opens the tab
`NAME` (`network`, `tables`, `results`, `issues`, `claude`, `settings`, or a
table name such as `bus`).
Unknown values are ignored, and a solver error is shown as usual.

Each case table can be imported from and exported to a data file
(*Import…*, *Export ▾*; `POST /api/table/import`, `GET /api/table/export`),
read and written with pandas in the format of the file extension (`.csv`,
`.tsv`, `.json`, optionally compressed, `.parquet`, `.feather`, `.xlsx`;
see `ppedit.server`). With no rows selected, import appends rows and export
writes all rows; with rows selected, import replaces them
(`ppedit.Case.import_rows`, same columns and row count) and export writes
only them.

The network diagram has a *Geodata* layout, available when the case has
geodata, that places buses at their locations on a map (Web Mercator) with
optional USGS satellite imagery, Census TIGERweb roads, and state and county
boundaries and names. Buses without geodata are drawn next to their located
neighbors (dashed) or left out. Clicking where several buses overlap lists
them to choose from.

A search box in the network toolbar finds a bus (by number or geodata
text) or a branch or DC line (by its two bus numbers, or all those at a
bus) and selects it for editing; several matches are listed to choose from.

What is not promised
--------------------

  - One case per server, held in memory, for one user on the local machine.
    There is no authentication: do not expose the server on a shared network.

  - Only the solver-layer GLM objects (`bus`, `gen`, `branch`, `gencost`,
    `dcline`) are edited; other `module pypower` objects (`load`,
    `powerplant`, ...) and other modules are reported on load and dropped on
    save. GLM preprocessor directives (`#include`, `#input`, `#begin`, ...)
    are not expanded; a GLM whose buses come only from them is refused with
    an error naming the files to open instead.

  - PSS/E `.raw` cases (revisions 23 to 35) are read, not written, and
    are converted as MATPOWER's `psse2mpc` does (see `ppedit._psse`):
    names and the data PYPOWER cannot hold (owners, zones, magnetizing
    admittance, impedance correction, switched shunt blocks other than the
    initial susceptance, VSC and multi-terminal DC lines, FACTS devices,
    ...) are listed when the case is opened and dropped; constant current
    and admittance loads become constant power at the case voltage; each
    3-winding transformer adds a star bus; there are no cost rows.

  - `areas` is not written to GLM (there is no GridLAB-D object for it).
    `dclinecost` is written to GLM as `dclinecost` runtime-class objects
    with the `pypower.gencost` properties, parent the DC line (GridLAB-D
    keeps them but does not solve with them). `areas` is not passed to the solver: the
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
    States only, and are not cached by `ppedit`. GLM output writes
    auxiliary geodata as `pp_geodata` objects; tags that are not GLM
    property names are renamed (e.g. `KV level` to `KV_level`). In the classic style a bus with both generation and load
    is drawn as a generator (circle); its label shows both MW values. The
    one-line style computes its own layered layout from the topology (it
    is not dragged); each line has a column at each of its buses joined by
    one horizontal segment, which keeps bus bars short but makes large
    meshed cases wide, and lines that skip rows still cross bus bars.
    Clicking a one-line load switch sets that bus's `Pd` and `Qd` to 0 and
    keeps their values in server memory (with the undo history) to restore
    on the next click; they are not saved in the case file, and editing
    `Pd` or `Qd`, renumbering or deleting the bus, or opening another case
    forgets them.
    Label choices (**Labels ▾**) are kept in the browser, not in the case,
    and labels are not placed to avoid each other, so many data labels on a
    dense diagram overlap.

  - Claude sessions send the case data they read, and the files and web
    pages they use, to the Anthropic API; they are kept in server memory
    only and end when the server stops. Changes Claude makes are checked
    like any other edit but not reviewed: look at them (and undo them)
    before saving.

  - Buses with no in-service branch are solved as isolated (type NONE)
    without changing the case.

  - Run OPF offers only the OPF solvers whose Python module is installed
    (`ppedit.case.solvers`); PIPS (AC and DC) ships with `pypower`, while
    IPOPT, CPLEX, MOSEK and Gurobi are offered when their modules are
    present but are not tested by ppedit. A DC OPF solution has flat
    voltages, no reactive power and no losses; applying it copies those
    into the case.

  - The cvxopf solvers (`CVXOPF-*`, offered when cvxopf is installed)
    report no LMPs or multipliers; ignore reactive power costs; model DC
    lines without `loss0`, terminal reactive power or voltage set points
    (the copper plate ignores DC lines and has no branch flows); and
    give the SOCP relaxation's voltages, which need not be AC feasible
    (the log says whether they are). The cvxopf DC network-flow model has no
    voltage angles. The cvxopf solve options (Settings tab, `options` of
    `ppedit.Case.solve`) cover numerical preparation, solver choice,
    solver output and solver options for one snapshot; cvxopf's
    multi-period horizons and device models are not offered.

  - The extended (result) columns shown in the tables after a solve
    (`ppedit.case.RESULT_COLUMNS`, the `extended` rows of
    `ppedit.Case.solve`) are not part of the case: they are not edited,
    saved, imported or kept after the next edit (table export includes
    them while they are shown). Multipliers are given
    only by the PYPOWER OPF solvers, as PYPOWER computes them.

  - Solver messages (`messages` of a solution, listed on the Issues tab)
    report what the solver reports and what ppedit changed to solve the
    case; they are not a diagnosis of why a solve failed. The `detail` of a
    warning (its physical meaning and the constraints involved) covers the
    warnings ppedit knows; other cvxopf and Python warnings have none, and
    its text may need revising as cvxopf changes.

Data
----

Columns follow `pypower.caseformat`; units are listed in `ppedit.case.COLUMNS`.

```mermaid
flowchart LR
    file[(.py / .json / .glm)] <--> Case
    raw[(PSS/E .raw)] --> Case
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
