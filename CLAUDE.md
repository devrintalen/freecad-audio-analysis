# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A FreeCAD workbench for electroacoustic simulation — headphones/earphones primarily,
loudspeakers secondarily. It is an orchestration layer: it prepares geometry, writes
solver input decks, runs external FOSS solvers as subprocesses, and presents results.

**`STRUCTURE.md` is the source of truth** for capabilities, the object model, solver
choices, and the tier roadmap. Read it before making design decisions. If a change
contradicts it, update `STRUCTURE.md` in the same commit.

**Current state: design phase.** No implementation code exists yet.

## The owner is not an audio engineer

Explain acoustics reasoning when it drives a decision — why thermoviscous losses matter in
a 0.1 mm slot, why an earphone can't terminate into free space, what a rayl is. Don't
assume domain vocabulary is shared. `STRUCTURE.md` §2 and the glossary carry the baseline;
extend the glossary rather than dropping unexplained jargon into code or docs.

## Non-negotiable conventions

**Units.** FreeCAD works internally in **mm**; every solver here expects **SI metres**.
Conversion happens in exactly one place per solver — the input-deck writer — and is covered
by tests. Getting this wrong yields plausible-looking wrong answers, not crashes. Never
scatter `/1000` through physics code.

**Never write a solver.** If a numerical kernel seems needed, find the FOSS project that
already has it. Native numerics are limited to lumped-network assembly, analytic reference
models, and post-processing.

**Solvers are subprocesses, never linked.** This is a licence requirement (Elmer, Gmsh and
OpenFOAM are GPL; this workbench is LGPL-2.1+). Communication is via files in a case
directory. That case directory must stay human-readable and hand-editable.

**Python-only.** FreeCAD requires external workbenches to contain no compiled extensions.

**Tier order.** `STRUCTURE.md` §5 defines five tiers. Do not start tier N+1 before tier N
has passing validation cases. Tier 1 (lumped) must keep working with zero external solver
binaries installed.

**Multiple drivers are first-class, from Tier 1.** The owner's own design is a two-way
over-ear headphone. Never write a single-driver code path: build the lumped engine as a
**general nodal network solver** (`STRUCTURE.md` §6.6), where a driver has two acoustic
ports and elements connect explicit nodes. Drivers sharing a volume load each other, so
they must be solved simultaneously — superposing independent single-driver runs is wrong,
and most wrong in the crossover region (§2.4). Solve natively in NumPy, not by generating
SPICE netlists: radiation and viscothermal impedances are not R/L/C.

**Report the lumped validity limit with every lumped result.** A cavity is only a lumped
element below roughly `c/(8L)` — about 400 Hz for a 105 mm over-ear cup. Use
`AirProperties.lumped_validity_limit()`. Plotting a confident curve to 20 kHz from a model
valid to 400 Hz is the easiest way for this tool to mislead.

**Missing binaries degrade gracefully.** A missing `ElmerSolver` disables the relevant
commands with an explanatory message — never a traceback.

**Ear geometry is a solved problem — use the right source.** See `STRUCTURE.md` §6.4.
- **Ear canal / concha / pinna simulator** → ITU-T P.57 (06/2021), a *free* standard whose
  annexes give full tabulated cross-sections. `acoupy_ears` (MIT) already implements it and
  emits Gmsh/STL. Depend on it; don't re-derive, and don't vendor the ITU tables.
- **Exterior head / pinna / torso** → HUTUBS (CC BY 4.0, 96 subjects) is the default;
  SONICOM (CC BY) is larger. Both are **blocked-meatus** — no ear canal — so they cannot
  model in-ear devices on their own.
- **IEC 60318** geometry stays paywalled, and commercial target curves (Harman etc.) are
  not redistributable. Impedance-based fixtures and user-loaded target curves cover those.

## Environment

Surveyed 2026-08-02. Re-check rather than trusting this list if something fails.

| Component | Status |
|---|---|
| FreeCAD | 1.1.1, `/usr/bin/FreeCAD` |
| FreeCAD Python bindings | `/usr/lib64/freecad/lib64` |
| FreeCAD modules | `/usr/lib64/freecad/Mod` (incl. `Fem`, `Plot`, `Material`) |
| matplotlib | 3.11.0 (available to FreeCAD's Python) |
| NumPy / SciPy | 2.4.6 / 1.17.1 |
| ngspice | `/usr/bin/ngspice` |
| Gmsh | **not installed** — `Mod/Fem/femmesh/gmshtools.py` is only the driver; the binary is separate |
| Elmer (`ElmerSolver`, `ElmerGrid`) | **not installed** — needed from Tier 2 |
| NumCalc | **not installed** — needed from Tier 4 |

`docs/SETUP.md` covers installing the missing pieces (Gentoo-specific). Run
`python3 scripts/check_env.py` to see current state rather than guessing.

**Python packages must be importable from inside FreeCAD.** FreeCAD 1.1.1 embeds the
system interpreter (3.13.14), so `pip install --user --break-system-packages <pkg>` lands
in `~/.local/lib/python3.13/site-packages` and is picked up automatically. A plain venv is
*not* visible to a GUI-launched FreeCAD without `PYTHONPATH`. Prefer portage where the
package exists.

### Running FreeCAD headlessly

Import the bindings into system Python — this is how tests run without a GUI:

```bash
PYTHONPATH=/usr/lib64/freecad/lib64 python3 -c "import FreeCAD; print(FreeCAD.Version())"
```

`import FreeCADGui` will not work in this mode. Keep GUI code (ViewProviders, task panels)
strictly separate from document-object and physics code so the latter stays testable.

Also available: `FreeCAD --console` for an interactive embedded interpreter.

## Reuse what FreeCAD already provides

Before writing anything, check whether the FEM workbench has it. Wrap, don't reimplement:

- Meshing — `femmesh.gmshtools`, `Fem::FemMeshObject`
- Elmer invocation — `femsolver/elmer/` (mesh export, `ElmerGrid`, process launching)
- Field post-processing — `Fem::FemPostPipeline`, the full VTK cut/clip/contour toolset
- Materials — the `Material` module's card system
- 2D plots — the `Plot` module, or matplotlib on Qt directly

**Important:** FreeCAD's Elmer integration exposes mechanics, thermal, electrostatics,
magnetodynamics and flow equations — **not acoustics**. We write our own SIF generator for
the acoustic equations while reusing everything else. Don't waste time looking for an
acoustics equation object that isn't there.

Isolate all FEM-workbench interaction behind a thin adapter module; those internals shift
between FreeCAD releases.

## Validation

`validation/` holds benchmark cases with known-independent answers and tolerances
(analytic duct, cavity modes, lossy tube, baffled piston, sealed/vented box, coupler
impedance). Every tier ships with its benchmarks passing.

Treat correlation against a physical measurement as the acceptance test for Tier 3. Until
then, be explicit that results are unvalidated — a simulation stack nobody has checked
against a real measurement is a plotting library.

## Git

- Remote: `git@github.com:devrintalen/freecad-audio-analysis.git`, branch `master`.
- Commit as work completes; push when a unit of work is coherent.
- Keep `STRUCTURE.md` and this file current in the same commit as the change they describe.
