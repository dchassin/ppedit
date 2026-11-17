[![Validation](https://github.com/dchassin/ppedit/actions/workflows/validate.yaml/badge.svg)](https://github.com/dchassin/ppedit/actions/workflows/validate.yaml)
[![Documentation](https://github.com/dchassin/ppedit/actions/workflows/deploy-pages.yaml/badge.svg)](https://github.com/dchassin/ppedit/actions/workflows/deploy-pages.yaml)

Browser-based editor for PYPOWER and Arras Energy `module pypower` models

`ppedit` runs a small local web server and opens an editor in your
browser. It reads and writes PYPOWER case files (`.py`, `.json`) and Arras
Energy GridLAB-D models that use `module pypower` (`.glm`), and opens
Siemens PTI PSS/E power flow cases (`.raw`, revisions 23 to 35; read only,
save them as `.py`, `.json` or `.glm`). You edit the bus,
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

Optional: the cvxopf OPF solvers (Run OPF ▾, `CVXOPF-*`) appear when
[cvxopf](https://github.com/cvxgrp/cvxopf) is installed. It needs Python
3.11+ and the IPOPT library, installed first (Ubuntu: `sudo apt-get install
coinor-libipopt-dev`; macOS: `brew install ipopt`):

    pip install git+https://github.com/cvxgrp/cvxopf

# Usage

    ppedit serve                 # new, empty case
    ppedit serve case14.glm      # edit a file; Save writes back to it
    ppedit serve case14.py --geodata case14_geodata.csv   # add bus locations
    ppedit convert case14.py case14.glm
    ppedit convert wecc240.raw wecc240.glm   # PSS/E input (read only)
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
      and loading colors appear after a solve. Each branch and DC line
      leaves its bus bar near that bar's end facing the other bus and
      reaches the other bar with at most one horizontal segment, so each
      bar spans only its own equipment. This style computes its own
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
      neighbors. Clicking where several buses are drawn on top of each
      other (e.g. at the same location) lists them to choose from. Saving to `.py` or `.json` stores the data under the case
      key `geodata` (rows of `BUS_I, LAT, LON, ...`) with the auxiliary
      column names under `geotags`; `.glm` keeps each bus's `latitude` and
      `longitude` and writes the auxiliary columns as `pp_geodata` objects
      (a runtime class, one object per row with its bus as parent; column
      names that are not GLM names are changed, e.g. `KV level` to
      `KV_level`). **Map ▸ Download CSV** exports the geodata.

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

    The **Labels ▾** menu (between the layout selector and **Areas**) turns
    the diagram labels on and off. For buses: the bus number, voltage,
    generation and load; for branches: power flow and loading (% of
    `rateA`, after a solve). Any column of the bus and branch tables can
    also be shown as a label (`name value unit`, e.g. `baseKV 345 kV` or
    `rateA 250 MVA`). Each style keeps its own choices: by default the
    simple style shows bus numbers only, and the classic and one-line styles
    show bus number, voltage, generation, load and flows. Power flow and
    loading labels need a solution, so they are greyed out (with a note in
    the menu) until **Run PF** or **Run OPF**. **None** clears every label
    of the current style and **Defaults** restores its standard ones. The
    choices are remembered by the browser.

    The search box to the right of **Map ▾** finds a bus or branch: type
    a query and press Enter. `N` or `bus N` finds bus N (or, if there is no
    bus N, the buses whose number contains N); `A-B`, `A B` or `A to B`
    finds the branches and DC lines between buses A and B (either
    direction), or, if no line joins them, lists the lines at A and B to
    choose from (with a note saying so); `branch N` finds the branches and DC lines connected to bus
    N; any other text finds the buses whose geodata columns (e.g. a name)
    contain it. A single match is selected for editing in the side panel
    and centered in the diagram; several matches open a list to choose from
    (mouse, or arrow keys and Enter; Escape closes it).
  - **Buses / Generators / Branches / Gen costs / DC lines / DC line
    costs / Areas**: spreadsheet-style editing; Enter or Tab commits, Escape
    cancels. Renumbering a bus updates the generators, branches, DC lines
    and area price reference buses that use it; deleting a bus asks before
    deleting its connections; generator cost rows follow their generators
    and DC line cost rows their DC lines. Renumbering an area in the Areas
    table moves its buses with it. Click a column header to sort the rows
    by it (again to reverse, a third time to restore the case order); the
    sort changes only the display, not the row order saved in the case.
  - **Run PF ▾** and **Run OPF ▾** (toolbar) each open a menu of solvers;
    choosing one runs it; while it runs, the button's ▾ turns into ● and
    the button is disabled until the solver returns. Run PF lists the PYPOWER power flow algorithms
    (Newton, fast-decoupled XB and BX, Gauss-Seidel). Run OPF lists the
    OPF solvers that are installed: AC OPF with PIPS (interior point or
    step-controlled) and IPOPT (needs `pyipopt`), and DC OPF with PIPS,
    IPOPT, CPLEX (`cplex`), MOSEK (`pymosek`) or Gurobi (`gurobipy`), and,
    with cvxopf installed, its four network models: full AC (IPOPT), the
    SOCP relaxation of AC, a lossy DC network-flow model and a copper
    plate (no branch flows). cvxopf reports no prices (LMPs), solves
    piecewise-linear costs, ignores reactive power costs, and models DC
    lines without `loss0`, terminal reactive power or voltage set points.
    `Case.solve(kind,algorithm)` takes the same choices (`ppedit.case.solvers()`
    lists them). A DC OPF solution has flat voltages (1 pu), no reactive
    power and no losses.
  - **Results**: PF/OPF summary (with the solver used), bus voltages (and LMPs for OPF), dispatch,
    branch flows and loading, DC line flows and losses, area totals, the
    PYPOWER report, and **Apply solution to case** to copy the solved
    Vm/Va/Pg/Qg and DC line flows into the model. Results also reports how
    the open file was read (format, PSS/E revision, rows read, and what was
    not converted or kept); opening a file with such notes shows this page.
    Click a results column header to sort by it (again to reverse, a third
    time to restore the solver order); the sort is kept across solves.
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
GridLAB-D has no DC line cost class, so `dclinecost` rows are written as
`dclinecost` objects (a runtime class with the `pypower.gencost`
properties, parent the DC line) and read back from them; GridLAB-D keeps
them but does not solve with them.

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

PSS/E `.raw` cases are converted as MATPOWER's `psse2mpc` does: loads,
fixed shunts and the initial switched shunt susceptance become bus
demand and shunts (constant current and admittance loads at the case
voltage), 2-winding transformers become branches with a turns ratio and
phase shift, each 3-winding transformer becomes three branches to an added
star bus (numbered from the next power of ten above the largest bus
number), and two-terminal DC lines become `dcline` rows. A generator that
regulates another bus gets its own bus voltage as `Vg` (PYPOWER regulates
only the generator bus), so a solved case solves to the same voltages,
unless generators are held at a reactive power limit in the case
(PYPOWER power flow does not enforce those; the notes say how many). Names, owners,
zones, magnetizing admittance, impedance correction, VSC and multi-terminal
DC lines, FACTS devices and the other PSS/E data PYPOWER cannot hold are
dropped, and the editor lists them when the file is opened. PSS/E has no
costs, so add `gencost` rows before running OPF. **Save** does not write
`.raw`: use **Save as** with `.py`, `.json` or `.glm`.

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

`ppedit --version` prints the installed version, e.g. `ppedit 0.3.1`.

## Python

    from ppedit import Case
    case = Case.builtin("case9")
    case.set_value("bus", 4, "Pd", 95)
    result = case.solve("opf")
    print(result["success"], round(result["objective"], 2))
    case.save("case9.glm")

outputs

    True 5422.68
