[![Validation](https://github.com/dchassin/ppedit/actions/workflows/validate.yaml/badge.svg)](https://github.com/dchassin/ppedit/actions/workflows/validate.yaml)
[![Documentation](https://github.com/dchassin/ppedit/actions/workflows/deploy-pages.yaml/badge.svg)](https://github.com/dchassin/ppedit/actions/workflows/deploy-pages.yaml)

Browser-based editor for PYPOWER and Arras Energy `module pypower` models

`ppedit` runs a small local web server and opens an editor in your
browser. It reads and writes PYPOWER case files (`.py`, `.json`) and Arras
Energy GridLAB-D models that use `module pypower` (`.glm`). You edit the bus,
generator, branch, generator-cost, DC line, DC line cost and area tables, see the network drawn as a
one-line diagram, get immediate validation (missing REF bus, dangling
references, zero-impedance branches, malformed cost curves, ...), and run
PYPOWER power flow and optimal power flow, with voltages and line loadings
shown on the diagram. It is for engineers preparing transmission and
sub-transmission models for Arras Energy.

# Documentation

See https://www.chassin.org/ppedit

# Installation

Install using `pip`:

    pip install git+https://github.com/dchassin/ppedit

# Usage

    ppedit serve                 # new, empty case
    ppedit serve case14.glm      # edit a file; Save writes back to it
    ppedit serve case14.py --geodata case14_geodata.csv   # add bus locations
    ppedit convert case14.py case14.glm
    ppedit check case14.glm

The editor opens at http://localhost:8050/. Start from **New ▸** to load
any PYPOWER built-in case (`case9`, `case14`, ..., `case300`), or **Open…**
to upload a file. **Save as ▸** downloads `.py`, `.json` or `.glm`.
**Exit** stops the server after asking for confirmation and closes the
browser tab when the browser allows it.

In the editor:

  - **Network**: drag buses, scroll to zoom, click a bus, branch or DC line
    to edit it in the side panel; add a generator, a branch, a DC line, or a
    new linked bus from the panel. DC lines are drawn as purple dash-dot
    lines (with a "DC" tag in the simple and classic styles and a converter
    box at each end in the one-line style). Three styles:
    - *Simple*: buses colored by type; after a solve, by voltage.
    - *Classic* (utility one-line diagram conventions): generator buses
      are circles, load buses triangles, other buses squares, and
      disconnected buses are drawn empty; transformers show as interlocking
      circles. Buses are colored by voltage magnitude (blue low, red high)
      and labeled with the bus number, voltage angle, and generation (G) and
      load (L) in MW. After a solve, lines are colored by loading as a
      percentage of `rateA` (blue light, red at or over the rating) and
      labeled with their MW flow, with an arrow in the direction of flow.
      Before a solve, the case's own Vm, Va and Pg values are shown.
    - *One-line* (utility one-line diagram layout): buses are horizontal
      bus bars and branches vertical lines, with power sources at the top
      and loads at the bottom (rows follow the distance from the nearest
      generator bus). Generators (circle with a sine wave) and loads
      (arrow) connect to their bus bar through dashed pseudo-lines with a
      disconnect switch showing their status (generator `status`; a load's
      switch is open when its bus is type NONE). Branches have a circuit
      breaker at each end (red filled closed, green hollow open) and
      transformers show as interlocking circles. Branches, generators and
      loads are labeled with MW and MVAr, positive downward (from the upper
      bus to the lower one, into a bus from a generator, out of a bus to a
      load), with an arrow in the direction of real power; branch flows
      and loading colors appear after a solve. This style computes its own
      layout (buses can't be dragged) and scales as a whole when zooming;
      labels are hidden when zoomed far out.

    Two layouts for the simple and classic styles:
    - *Force layout*: positions computed from the topology (drag to adjust;
      not saved).
    - *Geodata*: buses at their latitude/longitude on a map. Available only
      when the case has geodata: use **Map ▸ Load CSV…** (columns `BUS_I`,
      `LAT`, `LON` at a minimum; any other columns are kept as auxiliary
      data and shown in the bus panel), `--geodata`, or a case file with a
      `geodata` array. The **Map ▸** menu turns on satellite imagery (USGS
      The National Map), roads, and state and county boundaries and names
      (U.S. Census Bureau TIGERweb); these need network access and cover the
      United States. Buses without geodata are drawn dashed next to their
      neighbors. Saving to `.py` or `.json` stores the data under the case
      key `geodata` (rows of `BUS_I, LAT, LON, ...`) with the auxiliary
      column names under `geotags`; `.glm` keeps each bus's `latitude` and
      `longitude`. **Map ▸ Download CSV** exports the geodata.

    The **Areas** check box (enabled when the case has more than one area)
    draws a labeled region around the buses of each area and marks price
    reference buses with ★; bus colors don't change. Regions never overlap:
    each bus claims the space nearer to it than to any bus of another area
    (out to a radius set by the typical line length), and points along the
    lines inside an area join its buses into one region. The force layout
    groups each area's buses together; in the geodata layout an area whose
    buses are far apart shows as separate patches. The one-line style gives
    each area its own box of columns (areas left to right in number order);
    a line between two areas is drawn with a jog from its column in one box
    to its column in the other, so every bus bar stays inside its area.
  - **Buses / Generators / Branches / Gen costs / DC lines / DC line
    costs / Areas**: spreadsheet-style editing; Enter or Tab commits, Escape
    cancels. Renumbering a bus updates the generators, branches, DC lines
    and area price reference buses that use it; deleting a bus asks before
    deleting its connections; generator cost rows follow their generators
    and DC line cost rows their DC lines. Renumbering an area in the Areas
    table moves its buses with it.
  - **Results**: PF/OPF summary, bus voltages (and LMPs for OPF), dispatch,
    branch flows and loading, DC line flows and losses, area totals, the
    PYPOWER report, and **Apply solution to case** to copy the solved
    Vm/Va/Pg/Qg and DC line flows into the model.
  - **Issues**: every validation error and warning; click one to jump to
    the cell.
  - **Claude**: any number of conversations with Claude, each in its own tab (the
    Anthropic Messages API) that read and edit the open case, check it and
    run PF/OPF. Each session chooses a model and its data sources: the case
    only, the web (search and public data downloads), and/or the files in
    one folder on the server machine. Every change Claude makes is one
    undoable edit and shows up in the other tabs as it happens. Needs an
    API key in `ANTHROPIC_API_KEY` or `~/.anthropic` (see `ppedit.claude`).
  - Undo/redo (Ctrl+Z, Ctrl+Shift+Z) covers every edit; Ctrl+S saves.

DC lines follow the PYPOWER `dcline` format (`pypower.idx_dcline`) and are
solved as in `pypower.toggle_dcline`: each line is a pair of generators at
its terminal buses, which become PV buses held at the line's `Vf` and `Vt`.
Power flow keeps the line's `Pf` and applies its losses (`loss0 +
loss1*Pf`); OPF dispatches `Pf` within `Pmin`..`Pmax` at the `dclinecost`
cost. GLM files store DC lines as `module pypower` `dcline` objects;
GridLAB-D has no DC line cost objects, so `dclinecost` is kept only in
`.py` and `.json`.

Areas follow the PYPOWER `areas` format (`area`, `price_ref_bus`; see
`pypower.caseformat`) and list the areas named by the bus `area` column.
A case opened without an `areas` table (every `.glm` file, and PYPOWER
cases such as `case39`) gets one row per bus area, with the area's REF bus,
else its bus with the most generating capacity, else its first bus as price
reference bus.
After a solve, Results shows each area's generation, load, shunt
consumption, internal losses, net export over AC and DC ties, and, for OPF,
the price (λP) at its price reference bus. The `areas` table is not passed
to PYPOWER and is kept only in `.py` and `.json`: GridLAB-D has no area
objects (each GLM bus keeps its `area`, and the rows are rebuilt when the
file is opened).

The server listens on `127.0.0.1` only and has no authentication; it is a
single-user desktop tool.

# Maintenance

## Update docs

    make docs

## Run tests

    pip install -e . pytest
    pytest

The tests need no network or credentials; see
[`ppedit/_test/README.md`](ppedit/_test/README.md).

# Examples

## PYPOWER cases

[`examples/pypower/`](examples/pypower/) holds the PYPOWER standard test
cases (`case4gs` to `case300`) as the original `.py` files and as Arras
Energy `module pypower` GLM models, for example:

    ppedit serve examples/pypower/case118.glm

## Command line

    ppedit check ppedit/_test/data/case3.glm

outputs

    ppedit/_test/data/case3.glm: no issues found

## Python

    from ppedit import Case
    case = Case.builtin("case9")
    case.set_value("bus", 4, "Pd", 95)
    result = case.solve("opf")
    print(result["success"], round(result["objective"], 2))
    case.save("case9.glm")

outputs

    True 5422.68
