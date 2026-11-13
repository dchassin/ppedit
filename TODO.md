# Editor

- [x] Edit bus, gen, branch, gencost tables with validation (see `ppedit.case.Case`)
- [x] Read/write PYPOWER `.py` and `.json`, and Arras Energy `module pypower` `.glm`
- [x] Network diagram with inspector; PF/OPF results overlay
- [x] Undo/redo; save guard for GLM content that is not kept
- [x] PYPOWER example cases in `.py` and `.glm` (see `examples/pypower/`)
- [x] Utility one-line diagram style (shapes by equipment, angle/MW labels, loading colors, MW flow labels)
- [x] One-line layout style (horizontal bus bars, vertical branches, sources on top, breakers, switches, MW/MVAr labels)

- [ ] Preserve non-solver GLM objects (`load`, `powerplant`, `powerline`, `shunt`, ...) on round trip, ideally by editing them too
- [ ] Label overlap avoidance in the classic style on dense networks
- [ ] One-line style: shorter bus bars and fewer crossings on large meshed cases (e.g. bus splitting or column compaction)
- [x] Geodata layout: bus locations from CSV or case `geodata`/`geotags` (GLM `latitude`/`longitude`), drawn on a map with imagery, roads, state/county boundaries and names
- [ ] Edit geodata in the editor (drag buses in the geodata layout, a geodata table)
- [ ] Persist force-layout positions
- [x] `dcline` and `dclinecost` tables (edit, validate, draw, solve; GLM `dcline` objects)
- [x] `areas` table (edit, validate, area colors in the diagram, per-area results)
- [ ] Copy/paste of cell ranges to and from spreadsheets
- [ ] Multiple open cases (tabs) and a recent-files list

# Release

- [x] Create `dchassin/ppedit`
- [ ] Enable Pages (source: GitHub Actions); add `approved` label
- [ ] First release `26.11.0`
