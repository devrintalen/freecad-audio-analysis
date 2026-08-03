# FreeCAD Audio Analysis Workbench

Acoustic simulation for **headphones, earphones and loudspeakers**, inside FreeCAD.

The workbench does not implement solvers. It prepares geometry, sets up the physics,
drives existing open-source solvers as external processes, and presents the results as
the curves an acoustics engineer actually reads — sound pressure level, impedance,
directivity.

> **Status: early development.** Tiers 0 and 1 are complete and tested — lumped-element
> modelling works, including multiple drivers and crossovers. No 3D solve yet, and nothing
> has been checked against a physical measurement, so treat results as unvalidated. See
> [STRUCTURE.md](STRUCTURE.md) for the full plan.

---

## Why

Loudspeaker and headphone design is dominated by proprietary tools. The open-source
pieces to do it properly already exist — Elmer FEM has a thermoviscous acoustics solver,
NumCalc does fast exterior BEM, ngspice handles equivalent circuits — but nothing joins
them to CAD geometry. That join is what this project is.

Headphones are the harder case and the primary target: they work in very small, nearly
sealed volumes where viscous and thermal losses dominate. At 1 kHz the viscous boundary
layer in air is about 70 µm, so a 0.1 mm damping slot behaves as a resistance rather than
a spring. Ignoring that gets earphone response wrong by many dB, which is why plain
lossless Helmholtz modelling is not enough here.

## Capability tiers

Each tier is independently useful and ships on its own.

| Tier | Capability | Engine | Status |
|---|---|---|---|
| 0 | Workbench skeleton, document objects, solver discovery | — | **complete** |
| 1 | Lumped-element modelling: enclosures, ports, drivers, crossovers | NumPy | **complete** |
| 2 | Lossless interior acoustics: cavity modes, horns, waveguides | Elmer Helmholtz | planned |
| 3 | Thermoviscous acoustics: narrow slots, damping mesh, small cavities | Elmer LNS | planned |
| 4 | Exterior radiation, directivity, structural coupling | NumCalc BEM, Elmer | planned |
| 5 | Nonlinear distortion, aeroacoustics, optimisation | — | stretch |

## What works today

**Modelling.** Start from a template — open-back, closed-back, two-way, in-ear, sealed or
vented box — rather than a blank canvas, because choosing what a driver's back connects to
is the most consequential decision in a lumped model and the least visible when it is
wrong. Then wire drivers, cavities, ports, damping meshes, leaks and passive radiators into
an explicit network.

**Multiple drivers are first class.** Drivers sharing a volume load each other, so they are
solved simultaneously; running two single-driver models and adding the curves gives a
different, wrong answer, and it is most wrong in the crossover region. Crossovers are
active or passive, and a passive ladder is evaluated into the driver's real impedance
rather than the flat resistance its component values assumed — so the response you see is
the one you would build.

**Geometry drives the numbers.** Extract the air from your parts by subtraction and an
acoustic volume follows the CAD instead of being typed. Port areas and leak perimeters read
from referenced faces and edges.

**Results.** SPL, electrical impedance, diaphragm excursion against Xmax, phase and group
delay; a plain-language summary card; CSV and FRD export. Parameter sweeps overlay a family
of runs with a delta view, which is how "what do my rear vents actually do" gets answered.

**It says when not to believe it.** Every lumped result carries the frequency above which
the cavity stops behaving as a compliance — about 400 Hz for a 105 mm over-ear cup — and
plots grey out the region beyond it. Preflight checks explain, in physical terms, what is
wrong and what to do about it.

Underneath, from Tier 0: an analysis container, an Environment giving density, speed of
sound and boundary-layer thickness from temperature/pressure/humidity, solver discovery
that explains what is missing, and objects that round-trip through saved documents,
gaining properties added since.

Geometry is read from any container — `Part` primitives, PartDesign bodies, links,
`App::Part` groups and full **assemblies** with externally linked documents. To see what
an existing model offers before setting anything up:

```bash
PYTHONPATH=/usr/lib64/freecad/lib64 python3 examples/inspect_assembly.py my_design.FCStd --cavity
```

That lists the parts and volumes, the element size the audio band demands, and whether a
fluid domain can be extracted by subtraction — or whether the model is open and needs its
opening capped first, which for a headphone cup it invariably is.

## Installation

Requires FreeCAD 1.0 or newer.

```bash
git clone https://github.com/devrintalen/freecad-audio-analysis.git
ln -s "$PWD/freecad-audio-analysis" ~/.local/share/FreeCAD/v1-1/Mod/AudioAnalysis
```

Then restart FreeCAD and pick **Audio Analysis** from the workbench selector.

For the full development environment, including the external solvers needed from Tier 2
onward, see [docs/SETUP.md](docs/SETUP.md). Check what you have with:

```bash
python3 scripts/check_env.py
```

Tier 1 needs **no external solver at all** — the lumped network is solved natively in
NumPy, because several of its impedances (radiation, viscothermal slot) are not R/L/C and
would be awkward in SPICE. Elmer, Gmsh and NumCalc are only required by the tier that uses
them, and the workbench installs and runs without them.

## Development

```bash
python3 -m pytest tests/ -q      # unit and integration tests
python3 validation/run.py        # benchmarks, with tolerances, as a report
```

**Benchmarks are separate from tests.** A test asks whether the code does what its author
meant; a benchmark asks whether what the author meant is *true*, against an answer obtained
some other way — a closed-form alignment, analytic radiation impedance, or ngspice on the
same netlist. Tier 1 agrees with theory to better than 0.03% and with ngspice to about
3 parts per million. See [validation/README.md](validation/README.md), including what is
still missing: nothing here has been compared against a physical measurement.

The test suite splits in two. The pure-physics and unit tests run in any interpreter. The
integration tests need FreeCAD's bindings, which `tests/conftest.py` locates
automatically; they skip if it cannot find them.

Physics and document-object code never imports `FreeCADGui`, so it stays testable
headlessly. See [CLAUDE.md](CLAUDE.md) for the conventions that matter, particularly the
unit discipline — FreeCAD works in millimetres, every solver expects SI metres, and its
internal *pressure* unit is kilopascals.

## Licence

LGPL-2.1-or-later, matching FreeCAD. All solvers are invoked as separate processes over
files rather than linked, which keeps GPL solvers at arm's length. No proprietary
dependency anywhere in the chain.
