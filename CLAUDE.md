# CLAUDE.md

Guidance for Claude Code when working in this repository. This file is the **working
rules**: what to do, what never to do, and how to run things. It deliberately does not
explain the design — that lives next door.

## Which document owns what

| File | Owns | Consult it for |
|---|---|---|
| **`STRUCTURE.md`** | The design. **Source of truth.** | Capabilities, physics rationale, solver choices, the object model, tier roadmap, validation plan, risks, glossary |
| **`CLAUDE.md`** (this file) | The working rules and the dev loop | Conventions that must not be broken, where code lives, how to run tests and FreeCAD, current state |
| **`README.md`** | The user-facing pitch | What a user can do today, install instructions |
| **`docs/SETUP.md`** | The development machine | Installing FreeCAD, Elmer, Gmsh, NumCalc (Gentoo-specific) |
| **`validation/README.md`** | The benchmarks | What has been checked against an independent answer, and how closely |

If a change contradicts `STRUCTURE.md`, update `STRUCTURE.md` in the same commit. If a
change breaks a rule below, the rule wins — raise it rather than working around it.

## What this is

A FreeCAD workbench for electroacoustic simulation — headphones/earphones primarily,
loudspeakers secondarily. It is an orchestration layer: it prepares geometry, writes
solver input decks, runs external FOSS solvers as subprocesses, and presents results.
`STRUCTURE.md` §1 has the scope, in and out.

**Current state: Tiers 0 and 1 implemented and tested.** The lumped network solver,
crossovers, cavity extraction from CAD, parameter sweeps and result export all work; no 3D
solve exists yet. Nothing has been correlated against a physical measurement, so results
are unvalidated — say so.

**What is next**, in order (`STRUCTURE.md` §5 defines the tiers):

1. Install Elmer and Gmsh; drive them end to end from FreeCAD's FEM workbench on a stock
   example, proving the toolchain before depending on it.
2. Tier 2 — the Elmer SIF writer for the lossless Helmholtz equation, plus meshing.
3. Tier 3 — thermoviscous, and the measurement correlation that Tier 3 is gated on.

## The owner is not an audio engineer

Explain acoustics reasoning when it drives a decision — why thermoviscous losses matter in
a 0.1 mm slot, why an earphone can't terminate into free space, what a rayl is. Don't
assume domain vocabulary is shared. `STRUCTURE.md` §2 and its glossary carry the baseline;
extend the glossary rather than dropping unexplained jargon into code or docs.

## Non-negotiable conventions

**Units.** FreeCAD works internally in **mm**; every solver here expects **SI metres**.
Conversion happens in exactly one place — `physics/units.py`, called by physics code and
input-deck writers — and is covered by tests. Never scatter `/1000` through physics code.
Getting this wrong yields plausible-looking wrong answers, not crashes. The same trap
applies to pressure: FreeCAD's internal pressure unit is **kilopascals**, so a property set
to `101325 Pa` reads back as `101.325`.

**Never write a solver.** If a numerical kernel seems needed, find the FOSS project that
already has it (`STRUCTURE.md` §3 lists the portfolio). Native numerics are limited to
lumped-network assembly, analytic reference models, and post-processing.

**Solvers are subprocesses, never linked.** This is a licence requirement (Elmer, Gmsh and
OpenFOAM are GPL; this workbench is LGPL-2.1+). Communication is via files in a case
directory, and that directory must stay human-readable and hand-editable (§8).

**Python-only.** FreeCAD requires external workbenches to contain no compiled extensions.

**GUI code stays separate.** Physics and document-object code must never import
`FreeCADGui`; ViewProviders and task panels are the only place it belongs. This is what
keeps the suite runnable headlessly.

**Tier order.** Do not start tier N+1 before tier N has passing validation cases. Tier 1
(lumped) must keep working with zero external solver binaries installed.

**Multiple drivers are first-class, from Tier 1.** The owner's own design is a two-way
over-ear headphone. Never write a single-driver code path: the lumped engine is a **general
nodal network solver** (§6.6), where a driver has two acoustic ports and elements connect
explicit nodes. Drivers sharing a volume load each other and must be solved simultaneously
— superposing independent single-driver runs is wrong, and most wrong in the crossover
region (§2.4). Solve natively in NumPy, not by generating SPICE netlists: radiation and
viscothermal impedances are not R/L/C.

**Report the lumped validity limit with every lumped result.** A cavity is only a lumped
element below roughly `c/(8L)` — about 400 Hz for a 105 mm over-ear cup. Use
`physics/validity.py` and `AirProperties.lumped_validity_limit()`. Plotting a confident
curve to 20 kHz from a model valid to 400 Hz is the easiest way for this tool to mislead
(§6.6).

**Missing binaries degrade gracefully.** A missing `ElmerSolver` disables the relevant
commands with an explanatory message — never a traceback.

**Don't vendor licensed data.** Ear geometry is a solved problem and `STRUCTURE.md` §6.4
says where each piece comes from; the working rule is only this: depend on `acoupy_ears`
(MIT) for ITU-T P.57 geometry rather than re-deriving it or copying the ITU tables into
this repo, take exterior head/pinna meshes from the CC-licensed databases, and never ship
paywalled fixtures (IEC 60318) or commercial target curves (Harman) — those are loaded from
user files.

## Reuse what FreeCAD already provides

Before writing anything, check whether the FEM workbench has it. Wrap, don't reimplement:

- Meshing — `femmesh.gmshtools`, `Fem::FemMeshObject`
- Elmer invocation — `femsolver/elmer/` (mesh export, `ElmerGrid`, process launching)
- Field post-processing — `Fem::FemPostPipeline`, the full VTK cut/clip/contour toolset
- Materials — the `Material` module's card system
- 2D plots — the `Plot` module, or matplotlib on Qt directly

**Important:** FreeCAD's Elmer integration exposes mechanics, thermal, electrostatics,
magnetodynamics and flow equations — **not acoustics**. We write our own SIF generator for
the acoustic equations while reusing everything else (§3). Don't waste time looking for an
acoustics equation object that isn't there.

Isolate all FEM-workbench interaction behind a thin adapter module; those internals shift
between FreeCAD releases.

## Where the code lives

`STRUCTURE.md` §7 has the full layout including planned directories. What exists today:

| Path | Contents |
|---|---|
| `freecad/audio_analysis/physics/` | Solver-independent models: `air`, `network` (the nodal solver), `crossover`, `driver`, `validity`, `units` |
| `freecad/audio_analysis/objects/` | `FeaturePython` proxies — the §6.2 document tree |
| `freecad/audio_analysis/viewproviders/` | ViewProviders, including the tree topology of §6.6 |
| `freecad/audio_analysis/commands/` | Toolbar commands |
| `freecad/audio_analysis/results/` | Curve container, target curves, summary card, plotting, export |
| `freecad/audio_analysis/solvers/discovery.py` | Binary discovery and the graceful-degradation messages |
| `builder.py`, `templates.py`, `checks.py`, `cavity.py`, `geometry.py` | Network construction from templates, preflight checks (§6.8), cavity extraction and placement resolution (§6.5) |
| `tests/` | Pure-physics tests plus FreeCAD integration tests that skip without bindings |
| `validation/` | Benchmarks against independent answers (§9) |
| `examples/` | Runnable studies: `inspect_assembly.py`, `open_back_study.py`, `two_way_study.py` |
| `scripts/` | `check_env.py`, `devpath.py` |

**Positions from nested parts must go through `geometry.global_placement_of()`.** A child
of an assembly reports its `Shape` in local coordinates; volume survives that, positions do
not (§6.5). A probe placed without it lands somewhere plausible and wrong.

## Running things

```bash
python3 -m pytest tests/ -q      # unit + integration; integration skips without FreeCAD
python3 validation/run.py        # benchmarks against independent answers, with tolerances
python3 scripts/check_env.py     # what is installed, and which tier first needs it
```

Tests and examples import `scripts.devpath`, which pins the `freecad` namespace package to
this working tree. That matters because FreeCAD's bootstrap prepends every installed addon
to `sys.path`: without it, a module written five minutes ago appears not to exist until it
has been committed and pulled back into the addon directory. Don't bypass it.

### Running FreeCAD headlessly

Import the bindings into system Python — this is how tests run without a GUI:

```bash
PYTHONPATH=/usr/lib64/freecad/lib64 python3 -c "import FreeCAD; print(FreeCAD.Version())"
```

`import FreeCADGui` will not work in this mode. `FreeCAD --console` gives an interactive
embedded interpreter.

### Python packages must be importable from inside FreeCAD

FreeCAD 1.1.1 embeds the system interpreter (3.13.14), so
`pip install --user --break-system-packages <pkg>` lands in
`~/.local/lib/python3.13/site-packages` and is picked up automatically. A plain venv is
*not* visible to a GUI-launched FreeCAD without `PYTHONPATH`. Prefer portage where the
package exists.

## Environment

The development machine, surveyed 2026-08-02. **Run `python3 scripts/check_env.py` rather
than trusting this table** — and note that a fresh CI or cloud container has none of it,
which is expected: the pure-physics suite still runs, and the rest skips.

| Component | Status |
|---|---|
| FreeCAD | 1.1.1, `/usr/bin/FreeCAD` |
| FreeCAD Python bindings | `/usr/lib64/freecad/lib64` |
| FreeCAD modules | `/usr/lib64/freecad/Mod` (incl. `Fem`, `Plot`, `Material`) |
| matplotlib | 3.11.0 (available to FreeCAD's Python) |
| NumPy / SciPy | 2.4.6 / 1.17.1 |
| ngspice | `/usr/bin/ngspice` |
| Gmsh | **not installed** — FreeCAD ships `femmesh/gmshtools.py`, the driver only; the binary is separate |
| Elmer (`ElmerSolver`, `ElmerGrid`) | **not installed** — needed from Tier 2 |
| NumCalc | **not installed** — needed from Tier 4 |

`docs/SETUP.md` covers installing the missing pieces.

## Validation

`validation/` holds benchmark cases with independently known answers and explicit
tolerances; `STRUCTURE.md` §9 lists the planned set per tier and `validation/README.md`
records what currently passes and by how much. Every tier ships with its benchmarks
passing, and a reference is never a previous run of this code.

Treat correlation against a physical measurement as the acceptance test for Tier 3. Until
then, be explicit that results are unvalidated — a simulation stack nobody has checked
against a real measurement is a plotting library.

## Git

- Remote: `git@github.com:devrintalen/freecad-audio-analysis.git`, branch `master`.
- Commit as work completes; push when a unit of work is coherent.
- Keep `STRUCTURE.md`, `README.md` and this file current in the same commit as the change
  they describe.
