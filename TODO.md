# Editor

- [x] Edit bus, gen, branch, gencost tables with validation (see `ppedit.case.Case`)
- [x] Read/write PYPOWER `.py` and `.json`, and Arras Energy `module pypower` `.glm`
- [x] Network diagram with inspector; PF/OPF results overlay
- [x] Undo/redo; save guard for GLM content that is not kept
- [x] Utility one-line diagram style (shapes by equipment, angle/MW labels, loading colors, MW flow labels)

- [ ] Preserve non-solver GLM objects (`load`, `powerplant`, `powerline`, `shunt`, ...) on round trip, ideally by editing them too
- [ ] Label overlap avoidance in the one-line style on dense networks
- [ ] Persist network layout (e.g., GLM `latitude`/`longitude` or `geodata`)
- [ ] `dcline` and `areas` tables
- [ ] Copy/paste of cell ranges to and from spreadsheets
- [ ] Multiple open cases (tabs) and a recent-files list

# Release

- [x] Create `dchassin/ppedit`
- [ ] Enable Pages (source: GitHub Actions); add `approved` label
- [ ] First release `26.11.0`
