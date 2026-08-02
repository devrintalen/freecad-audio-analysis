# FreeCAD Audio Analysis Workbench

Acoustic simulation for **headphones, earphones and loudspeakers**, inside FreeCAD.

The workbench does not implement solvers. It prepares geometry, sets up the physics,
drives existing open-source solvers as external processes, and presents the results as
the curves an acoustics engineer actually reads — sound pressure level, impedance,
directivity.

> **Status: early development.** Tier 0 (plumbing) is complete and tested. No acoustic
> simulation is possible yet. See [STRUCTURE.md](STRUCTURE.md) for the full plan.

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
| 1 | Lumped-element modelling: enclosures, ports, drivers, crossovers | ngspice / NumPy | next |
| 2 | Lossless interior acoustics: cavity modes, horns, waveguides | Elmer Helmholtz | planned |
| 3 | Thermoviscous acoustics: narrow slots, damping mesh, small cavities | Elmer LNS | planned |
| 4 | Exterior radiation, directivity, structural coupling | NumCalc BEM, Elmer | planned |
| 5 | Nonlinear distortion, aeroacoustics, optimisation | — | stretch |

## What works today

Tier 0 proves the plumbing end to end:

- An **analysis container** holding one study, with several allowed per document
- An **Environment** object: enter temperature, pressure and humidity; get density, speed
  of sound, characteristic impedance, Prandtl number and boundary-layer thickness
- **Volume measurement** of selected solids, reported in litres, cm³ and m³
- **Solver discovery** that reports what is installed and explains what is missing
- Objects **round-trip through saved documents**, and files written by older versions
  gain properties added since

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

Tier 1 needs no external solver beyond ngspice; Elmer, Gmsh and NumCalc are only required
by the tier that uses them, and the workbench installs and runs without them.

## Development

```bash
python3 -m pytest tests/ -q
```

The suite splits in two. The pure-physics and unit tests run in any interpreter. The
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
