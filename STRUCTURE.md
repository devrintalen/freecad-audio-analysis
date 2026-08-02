# FreeCAD Audio Analysis Workbench — Structure & Capability Plan

> Status: **design document, no code yet.** This file defines what the workbench is
> eventually meant to do, which existing FOSS solvers do the heavy lifting, and how all
> of it surfaces as objects and commands inside FreeCAD.

---

## 1. Purpose and scope

A FreeCAD workbench for **electroacoustic transducer simulation** — modelling how a
driver, its enclosure/housing, and the air volumes around it turn an electrical signal
into sound pressure at a listener's ear.

Primary target: **headphones and earphones** (in-ear, on-ear, over-ear).
Secondary target, sharing ~90% of the machinery: **loudspeakers** (sealed, ported,
passive radiator, transmission line, horn) and their radiation into free space.

### In scope

- Linear frequency-domain acoustics (the workhorse — this is where 95% of design work lives)
- Viscous and thermal losses in small volumes, slots, vents and mesh screens
- Structural vibration of diaphragms and enclosure panels, coupled to the air
- Electrodynamic motor behaviour (BL, Re, Le) coupled to mechanics and acoustics
- Lumped-element (equivalent circuit) modelling as a fast first pass
- Exterior radiation, directivity, and baffle diffraction
- Standard measurement fixtures (ear simulators, baffles) and standard result plots

### Explicitly out of scope (at least initially)

- Room acoustics / auralisation — different problem class, other tools do it well
- Large-signal nonlinear distortion (THD from BL(x), Cms(x), Le(i)) — see §5, Tier 5
- Aeroacoustic noise (port chuffing, turbulence) — see §5, Tier 5
- Signal processing / DSP tuning chains — belongs in a measurement tool, not a CAD workbench
- Anything requiring a proprietary solver

### Design principles

1. **Do not write solvers.** Every physics kernel comes from an existing, validated FOSS
   project. This workbench is geometry preparation, physics setup, job orchestration, and
   result presentation.
2. **Geometry is the input.** The whole reason to live inside FreeCAD is that the user
   already has the CAD model. Simulation setup should reference solids and faces directly,
   and survive geometry edits.
3. **Cheap before expensive.** A lumped model that runs in 50 ms should always be available
   alongside the 3D FEM run that takes 40 minutes. They should share the same parameter
   objects.
4. **Results are curves, not just colours.** An acoustics user wants SPL-vs-frequency,
   impedance, and polar plots far more often than a pretty field animation.

---

## 2. Physics primer — what actually has to be simulated

Written for someone who knows CAD and FEM but not audio engineering. This section
justifies the solver choices in §4.

### 2.1 The chain

```
electrical  →  magnetic/motor  →  mechanical  →  acoustic  →  ear/microphone
  V, I           BL, Le            diaphragm      pressure      SPL at a
                                   mass, comp.    field         defined point
```

Every stage loads the one before it: the air pushes back on the diaphragm, the diaphragm
motion induces back-EMF in the coil. A useful simulation is therefore **two-way coupled**
across at least the mechanical–acoustic interface, and ideally the electrical one too.

### 2.2 Why headphones are the *hard* case

Loudspeakers radiate into effectively infinite space; the interesting difficulty is the
unbounded domain. Headphones instead work in **very small, nearly-sealed volumes**, which
brings in effects that are negligible at loudspeaker scale:

**Viscous and thermal boundary layers.** Air next to a wall is slowed by viscosity and
heat-exchanges with the wall. The affected layer thickness is

$$\delta_v = \sqrt{2\mu/\rho\omega} \qquad \delta_t = \delta_v/\sqrt{\mathrm{Pr}}$$

In air that is **≈70 µm viscous / ≈82 µm thermal at 1 kHz**, and **≈220 µm at 100 Hz**.
In a 0.1 mm damping slot or a 0.05 mm gap around a diaphragm, the boundary layers fill the
entire channel — the air behaves as a lossy resistance, not a lossless spring. Ignoring
this gets earphone response wrong by many dB. This is the single biggest reason plain
Helmholtz (lossless wave equation) FEM is insufficient here and why *thermoviscous* /
*linearised Navier–Stokes* capability is a hard requirement.

**Acoustic resistance elements.** Damping meshes, felts, and fabrics are the primary
tuning tool in earphone design. They are not resolved geometrically; they are modelled as
a **transfer impedance** across a surface (a resistance in rayls, sometimes with a mass
term), or as an equivalent porous medium.

**Leakage.** The seal between an earphone and the ear canal is never perfect. The leak is
a thin slit forming a high-pass filter with the back volume, and it dominates bass
response. It is modelled as a lumped resistance + mass, or a resolved thin channel.

**The ear itself is a load, not free space.** Terminating a headphone model into "open air"
is meaningless. It must terminate into either a standardised ear simulator or an ear canal
model. See §6.4.

### 2.3 Loudspeaker-specific concerns

- **Unbounded radiation.** The domain has no outer wall. Handled either with a
  perfectly-matched layer (PML) on a truncated FEM mesh, or — usually better — with a
  boundary element method (BEM) that only meshes the surface and satisfies the radiation
  condition exactly.
- **Baffle diffraction.** Cabinet edges cause frequency-dependent ripples ("baffle step")
  in the on-axis response. Falls out naturally from a BEM/FEM exterior solve.
- **Directivity.** Off-axis response vs. frequency, presented as polar plots or a
  directivity ("sonogram") map. Requires evaluating the far field on a sphere of points.
- **Enclosure alignments.** Sealed / bass-reflex / passive radiator / transmission line /
  horn. Below a few hundred Hz these are captured perfectly by a lumped model; above that
  the internal standing waves need 3D.
- **Panel resonance.** Cabinet walls flex and radiate. Needs a structural–acoustic coupled
  solve.

### 2.4 The frequencies that matter

Audio band is 20 Hz – 20 kHz. At 20 kHz the wavelength in air is 17 mm, so element size
must be ~2–3 mm to get 6–10 elements per wavelength. For an over-ear cup that is a large
but tractable FEM problem; for a full head + torso exterior problem it is precisely why
BEM with fast multipole acceleration exists.

Runs are **frequency sweeps**: the same linear system assembled and solved at 50–500
discrete frequencies. This is embarrassingly parallel and the orchestration layer must
exploit that.

---

## 3. What existing tools give us

Rather than a monolith, the workbench drives a **portfolio of solvers**, choosing per
problem class.

| Tool | Licence | Role here | Maturity |
|---|---|---|---|
| **[Elmer FEM](https://elmerfem.org/)** | LGPL (lib) / GPL | Primary 3D solver: Helmholtz, thermoviscous acoustics, elasticity, structural–acoustic coupling, magnetostatics | Mature, actively developed, **already shipped with FreeCAD's FEM workbench** |
| **[Gmsh](https://gmsh.info/)** | GPL | Meshing, incl. boundary-layer refinement | Mature, **already bundled in FreeCAD** |
| **[NumCalc](https://github.com/Any2HRTF/Mesh2HRTF)** (from Mesh2HRTF) | EUPL v1.2+ | Exterior BEM with Burton–Miller + ML-FMM; the fast path for radiation/directivity/HRTF | Mature, purpose-built for acoustics, actively developed |
| **[Bempp-cl](https://bempp.com/)** | Permissive (verify per release) | Alternative/second-opinion BEM, scriptable from Python, good for interior–exterior coupling | Active, research-grade |
| **[ngspice](https://ngspice.sourceforge.io/)** | BSD-style | Lumped equivalent-circuit solving (Thiele–Small, crossovers, transducer networks) | Very mature |
| **[pyfar](https://pyfar.org/) / sofar** | MIT | Frequency-response objects, smoothing, plotting, SOFA I/O for directivity data | Active |
| **NumPy / SciPy** | BSD | Native lumped-network assembly, analytic models, post-processing | — |
| **VTK** | BSD | Field visualisation — **already FreeCAD's FEM post-processing pipeline** | — |
| **[FEniCSx](https://fenicsproject.org/) / scikit-fem** | LGPL / BSD | Escape hatch for formulations Elmer lacks | Active |
| **[OpenFOAM](https://www.openfoam.com/)** | GPL | *Tier 5 only* — aeroacoustic port noise | Mature, heavyweight |

### Why Elmer is the anchor

- It is the **only** mature FOSS FEM code with a first-class time-harmonic acoustics
  solver that includes **viscous and thermal losses** (its `AcousticsSolver` solves the
  linearised Navier–Stokes system; `HelmholtzSolver` handles the lossless case). That
  capability is the gate for credible earphone work.
- It is genuinely multiphysics — elasticity, magnetostatics, and acoustics in one solve
  with proper coupling, which is what a transducer needs.
- FreeCAD **already** ships Elmer integration: mesh export via Gmsh, `ElmerGrid`
  conversion, and `ElmerSolver` invocation. We inherit a working toolchain.

**Important caveat:** FreeCAD's existing Elmer integration exposes equations for
mechanics, heat, electrostatics, magnetodynamics and flow — **not acoustics**. So we write
our **own SIF (solver input file) generator** for the acoustic equations, while reusing
FreeCAD's mesh export and process-launching machinery. Upstreaming acoustic equations into
FreeCAD's FEM workbench is a possible later contribution, but should not be a dependency.

### Why also BEM

FEM must mesh the air volume and truncate it with a PML — expensive for exterior problems
and fiddly to set up. BEM meshes only surfaces and handles infinity exactly. For "what is
the polar response of this loudspeaker in free space" or "what does this earcup do to
sound arriving from outside", BEM is an order of magnitude cheaper. NumCalc is the
pragmatic choice: it is C++, standalone, CLI-driven, ML-FMM accelerated, and has been
validated on exactly this problem class by the HRTF community.

### Licence posture

The workbench itself will be **LGPL-2.1+** to match FreeCAD. All solvers are invoked as
**separate processes over files** — no linking — which keeps GPL solvers (Elmer's binary,
Gmsh, OpenFOAM) at arm's length. No proprietary dependency anywhere in the chain.

---

## 4. Solver selection matrix

Which engine handles which question:

| Design question | Engine | Typical runtime |
|---|---|---|
| Sealed/ported box bass alignment | Lumped (ngspice or native) | < 1 s |
| Earphone response with mesh + leak, ≤ ~2 kHz | Lumped network | < 1 s |
| Impedance curve of a driver | Lumped | < 1 s |
| Crossover network response | Lumped | < 1 s |
| Standing waves in an earcup / horn interior | Elmer Helmholtz | minutes |
| Earphone front volume + nozzle + coupler, full band | Elmer thermoviscous | tens of minutes |
| Damping-slot / vent resistance from geometry | Elmer thermoviscous (small local model) | minutes |
| Diaphragm break-up modes | Elmer elasticity (modal) | minutes |
| Diaphragm break-up → radiated response | Elmer coupled structural–acoustic | tens of minutes |
| Cabinet panel radiation | Elmer coupled structural–acoustic | tens of minutes |
| Free-space polar / directivity | NumCalc BEM | minutes–hours |
| Baffle diffraction | NumCalc BEM | minutes |
| Passive isolation of an earcup | NumCalc BEM (+ Elmer for the seal) | minutes–hours |
| Motor BL and inductance from magnet geometry | Elmer magnetostatics | minutes |

A key workflow consequence: **the lumped model and the 3D model must share parameters.**
A `Driver` object holds Thiele–Small parameters; the lumped solver consumes them directly,
and the 3D solver uses them to drive the diaphragm boundary. Better still, the 3D solver
can *extract* them (§6.5), closing the loop.

---

## 5. Capability tiers (development order)

Each tier is independently useful. Do not start tier N+1 before tier N is validated.

### Tier 0 — Skeleton
Installable external workbench; addon-manager metadata; document object base classes with
save/restore; solver discovery and a preferences page; a "hello world" analysis that meshes
a solid and reports its volume. **Goal: the plumbing is proven before any physics.**

### Tier 1 — Lumped element modelling
No 3D solve at all. Driver (Thiele–Small), enclosure volumes, ports, passive radiators,
acoustic resistances, leaks, crossover components. Assemble the equivalent circuit,
solve per frequency, plot SPL / impedance / cone excursion / group delay. Volumes can be
**measured from FreeCAD solids**, which is already more than most FOSS tools offer.
Validate against closed-form sealed and vented box theory.

### Tier 2 — Lossless interior acoustics (Elmer Helmholtz)
Mesh an air cavity, apply rigid walls / prescribed velocity / impedance boundaries, sweep
frequency, report pressure at probe points and as a field. Covers earcup modes, horn
throat behaviour, waveguides. Validate against analytic duct and rectangular-room modes.

### Tier 3 — Thermoviscous acoustics
Switch to Elmer's linearised Navier–Stokes solver with automatic boundary-layer mesh
refinement. Adds narrow-slot losses and small-cavity damping. Adds the transfer-impedance
boundary condition for mesh/fabric screens and the Johnson–Champoux–Allard model for
porous materials. **This is the tier that makes earphone work credible.** Validate against
analytic Kirchhoff tube attenuation and known coupler impedance curves.

### Tier 4 — Exterior radiation and coupled structure
NumCalc BEM for free-field radiation, far-field evaluation on a sphere, polar plots and
directivity maps. Elmer structural–acoustic coupling for diaphragm break-up and panel
radiation. Elmer magnetostatics for BL extraction. Validate against the analytic
baffled-piston and pulsating-sphere solutions.

### Tier 5 — Stretch
Large-signal nonlinear distortion (time-domain lumped with BL(x), Cms(x), Le(i));
aeroacoustic port noise via OpenFOAM; optimisation loops over geometry parameters;
active DSP/feedback ANC modelling; measurement import and model correlation.

---

## 6. How it appears inside FreeCAD

### 6.1 Workbench identity

- Name: **Audio Analysis** (internal: `AudioAnalysis`)
- Delivered as a **Python-only external workbench** via the Addon Manager
  (`package.xml` metadata), per FreeCAD's external-workbench requirement.
- Deliberately modelled on the FEM workbench's conventions so it feels native: an
  *analysis container* holding *feature objects*, a *mesh*, a *solver*, and *result*
  objects, driven from a toolbar plus task panels.

### 6.2 Document object model

All objects are `FeaturePython` / `DocumentObjectGroupPython` with custom ViewProviders,
so they serialise into the `.FCStd` file and appear in the model tree.

```
AudioAnalysis                       ← container, one per study
├── Environment                     ← air properties: ρ, c, μ, T, humidity, static P
├── Geometry & meshing
│   ├── AcousticDomain              ← a solid → fluid region (references CAD solid)
│   ├── SolidDomain                 ← a solid → elastic region (diaphragm, cabinet wall)
│   ├── MeshRegion                  ← local sizing / boundary-layer refinement
│   └── AcousticMesh                ← Gmsh-generated mesh (reuses FreeCAD FEM's mesh obj)
├── Materials
│   ├── FluidMaterial               ← air, helium, custom
│   ├── SolidMaterial               ← Mylar, aluminium, ABS … (E, ν, ρ, damping)
│   └── PorousMaterial              ← JCA parameters, or measured flow resistivity
├── Sources & transducers
│   ├── Driver                      ← moving-coil / balanced-armature / planar / ESL
│   │                                 holds T-S params, motor data, diaphragm ref
│   ├── VelocitySource              ← prescribed normal velocity on a face
│   ├── PressureSource              ← prescribed pressure on a face
│   └── PointSource                 ← monopole, for scattering/isolation studies
├── Boundaries
│   ├── RigidWall                   ← default; usually implicit
│   ├── ImpedanceBoundary           ← complex Z(f), or from a PorousMaterial
│   ├── TransferImpedance           ← damping mesh / screen across an internal surface
│   ├── LeakPath                    ← parametric slit: length, gap, perimeter
│   ├── RadiationBoundary           ← PML / infinite element / BEM handoff
│   └── SymmetryPlane
├── Lumped network                  ← Tier 1; can coexist with the 3D model
│   ├── AcousticVolume              ← compliance; volume auto-read from a CAD solid
│   ├── Port                        ← mass + loss, from CAD or parametric
│   ├── PassiveRadiator
│   ├── AcousticResistance
│   └── CrossoverNetwork            ← R/L/C ladder
├── Fixtures
│   ├── EarSimulator                ← IEC 60318-1 / -4 / -5 model (see §6.4)
│   ├── EarCanalModel               ← parametric or scanned canal geometry
│   ├── InfiniteBaffle
│   └── FreeField
├── Probes
│   ├── PressureProbe               ← point mic, e.g. drum reference point
│   ├── FieldProbe                  ← plane/volume for visualisation
│   └── FarFieldSphere              ← evaluation grid for directivity
├── Study
│   ├── FrequencySweep              ← log/linear/octave-fraction, or explicit list
│   └── ParameterSweep              ← vary a named expression across runs
├── Solvers                         ← one or more, coexisting
│   ├── SolverLumped
│   ├── SolverElmerAcoustic         ← lossless | thermoviscous | coupled
│   └── SolverBemNumCalc
└── Results
    ├── ResponseResult              ← SPL/impedance/phase curves per probe
    ├── FieldResult                 ← VTK pipeline object (reuses FEM post-processing)
    ├── PolarResult                 ← directivity data, SOFA-exportable
    └── ModalResult                 ← mode shapes + frequencies
```

**Reuse note:** `AcousticMesh` and `FieldResult` should wrap FreeCAD's existing
`Fem::FemMeshObject` and `Fem::FemPostPipeline` rather than reimplementing them. That
gives us Gmsh meshing, mesh visualisation, and the whole VTK cut/clip/contour toolset for
free.

### 6.3 UI surfaces

**Toolbars / menus**, grouped to match the tree:

1. *Analysis* — new analysis, set environment, activate analysis
2. *Model* — add fluid domain, solid domain, material, mesh region
3. *Sources* — driver, velocity/pressure source, point source
4. *Boundaries* — impedance, transfer impedance, leak, radiation, symmetry
5. *Lumped* — volume, port, passive radiator, resistance, crossover
6. *Fixtures* — ear simulator, ear canal, baffle, free field
7. *Probes* — point probe, field probe, far-field sphere
8. *Solve* — mesh, define sweep, run, job monitor
9. *Results* — response plot, polar plot, field view, export

**Task panels** for every object, following FreeCAD's `TaskPanel` idiom, with geometry
reference pickers (select faces/solids in the 3D view) as the primary input mode.

**Results viewer.** A dockable panel with:
- SPL vs frequency (log-x, dB-y), overlaying multiple probes and multiple runs
- Electrical impedance magnitude + phase
- Cone excursion vs frequency, with an Xmax limit line
- Group delay / phase
- Polar plots and directivity sonograms
- Cursor readout, fractional-octave smoothing, CSV / FRD export

Implemented with matplotlib on Qt (matplotlib ships with FreeCAD), with `pyfar` for
smoothing, resampling, and SOFA export.

**Expression support.** Every numeric property must be an `App::Property*` participating
in FreeCAD's expression engine, so a spreadsheet can drive an entire design study.

### 6.4 Measurement fixtures — an honest note

Meaningful headphone results require terminating into a **standardised ear simulator**:

- **IEC 60318-1** — artificial ear for supra-aural/circumaural headphones
- **IEC 60318-4** — occluded-ear simulator ("711 coupler"), the in-ear standard
- **IEC 60318-5** — 2 cm³ coupler, hearing-aid work

The dimensioned geometries live in paywalled IEC standards, and real fixtures
(GRAS, B&K) are proprietary. We therefore support three routes, in this order:

1. **Impedance-based** *(default, always available)* — represent the simulator by its
   published/analytic acoustic input impedance as a `TransferImpedance` termination or a
   lumped network. Correct to the frequency where the standard's tolerance applies, and
   legally unencumbered.
2. **User-supplied geometry** — the user imports their own CAD of a fixture they own or
   are licensed for.
3. **Parametric ear canal** — a generated canal + eardrum-impedance termination, useful
   for anatomical studies where standards conformance is not the goal.

Target curves (Harman, diffuse-field, free-field) are **loaded as user data files**, not
shipped, for the same reason. The workbench provides the overlay and deviation-scoring
machinery.

### 6.5 Round-tripping between lumped and 3D

The feature that makes the two-tier approach worth the effort:

- **3D → lumped:** run a small local FEM model of a vent, slot, or mesh screen and extract
  its complex acoustic impedance; write it back into the lumped `AcousticResistance`.
  Likewise extract T–S parameters from a coupled motor + diaphragm solve.
- **Lumped → 3D:** use the lumped model's diaphragm velocity as the boundary condition for
  a 3D interior solve, avoiding a full coupled solve when the driver is well characterised.

This is how commercial tools are actually used, and it is where a lot of the practical
value sits.

---

## 7. Repository layout

```
freecad-audio-analysis/
├── package.xml                 # Addon Manager metadata
├── LICENSE                     # LGPL-2.1+
├── README.md
├── STRUCTURE.md                # this file
├── InitGui.py                  # workbench registration (FreeCAD entry point)
├── Init.py
├── freecad/
│   └── audio_analysis/
│       ├── __init__.py
│       ├── commands/           # one module per toolbar command
│       ├── objects/            # FeaturePython proxies (the §6.2 tree)
│       ├── viewproviders/      # ViewProvider classes + task panel hooks
│       ├── taskpanels/         # Qt UI + .ui files
│       ├── physics/            # solver-independent models
│       │   ├── air.py          # ρ, c, μ, κ vs T / P / humidity
│       │   ├── lumped.py       # network assembly + frequency solve
│       │   ├── porous.py       # Johnson–Champoux–Allard, Delany–Bazley
│       │   ├── slits.py        # analytic viscothermal slit/tube impedance
│       │   └── analytic.py     # piston, sphere, tube — validation references
│       ├── solvers/
│       │   ├── base.py         # common job interface: prepare/run/parse
│       │   ├── elmer/          # SIF writer, mesh export, result reader
│       │   ├── numcalc/        # BEM input writer + result reader
│       │   └── spice/          # netlist writer for ngspice
│       ├── meshing/            # Gmsh driver, boundary-layer sizing heuristics
│       ├── results/            # curve/field containers, smoothing, export
│       ├── plotting/           # matplotlib panels
│       ├── resources/          # icons, translations, material libraries
│       └── tests/
├── examples/                   # worked FCStd models per tier
├── validation/                 # benchmark cases + expected results + tolerances
└── docs/
```

---

## 8. Execution architecture

```
FreeCAD document objects
        │  (write)
        ▼
Case directory  <working dir>/<analysis>/<solver>/<run-id>/
        │  mesh files, solver input decks, per-frequency job scripts
        ▼
External solver process  (QProcess, non-blocking, cancellable)
        │  stdout parsed for progress
        ▼
Result files  (.vtu, ASCII tables, SOFA)
        │  (read)
        ▼
Result objects in the document  →  plots + VTK pipelines
```

Requirements on this layer:

- **Never block the GUI.** Solvers run as child processes with streamed progress into a
  job monitor panel; the user can keep editing.
- **Frequency-parallel.** A sweep is N independent solves. Dispatch them across cores
  (and, later, optionally to a remote machine over SSH).
- **Reproducible and inspectable.** The case directory is a complete, self-contained,
  human-readable input deck. Power users must be able to hand-edit a SIF and re-run.
- **Cached.** Hash the geometry, mesh, physics setup, and frequency list. Unchanged inputs
  reuse existing results rather than re-solving.
- **Unit discipline.** FreeCAD's internal length unit is **mm**; every solver here expects
  **SI (m)**. Conversion happens at exactly one place — the solver input writer — and is
  covered by tests. This is the single most likely source of silent, plausible-looking
  wrong answers.
- **Graceful degradation.** If a solver binary is missing, the relevant commands are
  disabled with an explanatory message, not a traceback. Tier 1 must work with zero
  external binaries beyond what FreeCAD already ships.

---

## 9. Verification and validation

Every tier ships with benchmarks whose answers are known independently. Stored under
`validation/` with tolerances and run in CI where possible.

| Case | Reference |
|---|---|
| Plane wave in a rigid duct | Analytic |
| Rectangular cavity modes | Analytic eigenvalues |
| Lossy narrow tube attenuation | Kirchhoff / low-reduced-frequency solution |
| Pulsating and oscillating sphere | Analytic |
| Rigid piston in an infinite baffle | Analytic (on-axis and directivity) |
| Sealed and vented box response | Closed-form Thiele–Small alignment |
| Helmholtz resonator | Analytic + measured |
| 711-coupler input impedance | IEC 60318-4 published tolerance band |
| A real headphone the user owns | Measured response |

The last one matters most. **A simulation stack nobody has correlated against a physical
measurement is a plotting library.** Plan to measure at least one driver and one finished
headphone early, and treat that correlation as the acceptance test for Tier 3.

---

## 10. Key risks

| Risk | Mitigation |
|---|---|
| Elmer's thermoviscous solver is expensive and can be numerically stiff | Restrict it to small sub-models; use lossless Helmholtz + lumped losses elsewhere; consider the low-reduced-frequency approximation as a middle tier |
| Boundary-layer meshing is fiddly and easy to get wrong | Automate layer thickness from the top sweep frequency; warn loudly when a region is under-resolved |
| Material data (mesh resistances, diaphragm damping) is rarely published | Provide impedance-extraction workflows and a user-editable material library; accept measured data as input |
| Standardised fixture geometry is not freely available | Impedance-based fixtures as the default path (§6.4) |
| Scope creep — this could become COMSOL | Tiered plan; each tier independently shippable; stretch items explicitly quarantined in Tier 5 |
| FreeCAD FEM internals shift between releases | Wrap all FEM-workbench interaction behind a thin adapter module; pin a minimum FreeCAD version |

---

## 11. Current environment

Surveyed 2026-08-02 on the development machine:

| Component | Status |
|---|---|
| FreeCAD | **1.1.1** at `/usr/bin/FreeCAD`, FEM module present at `/usr/lib64/freecad/Mod/Fem` |
| ngspice | installed (`/usr/bin/ngspice`) |
| NumPy / SciPy | 2.4.6 / 1.17.1 |
| Gmsh | **not on PATH** — check whether FreeCAD bundles it internally; otherwise install |
| Elmer (`ElmerSolver`, `ElmerGrid`) | **not installed** — required from Tier 2 onward |
| NumCalc | **not installed** — required from Tier 4 onward |

FreeCAD 1.1 is a good baseline: the FEM workbench core was substantially reworked for 1.0
to make adding solvers easier, which is exactly the seam we hook into.

## 12. Immediate next steps

1. Install Elmer and Gmsh; confirm FreeCAD's FEM workbench can drive them end to end on a
   stock example. This validates the toolchain before we depend on it.
2. Build Tier 0: installable skeleton workbench, one analysis container, one command.
3. Build Tier 1 lumped engine + response plot, validated against sealed/vented box theory.
   This needs nothing beyond what is already installed.
4. Only then start the Elmer SIF writer.

---

## Glossary

- **SPL** — sound pressure level, dB re 20 µPa
- **Thiele–Small parameters** — the ~6–10 lumped parameters (Fs, Qts, Vas, Re, BL, Mms,
  Cms, Sd, Xmax) that describe a driver's low-frequency behaviour
- **BL** — force factor, magnetic flux density × coil length; N/A or T·m
- **Sd** — effective radiating area of the diaphragm
- **Xmax** — maximum linear excursion of the diaphragm
- **Rayl** — unit of specific acoustic impedance, Pa·s/m
- **Helmholtz equation** — the lossless wave equation in the frequency domain
- **Thermoviscous / LNS** — acoustics including viscous and thermal losses
- **BEM** — boundary element method; meshes only surfaces, handles infinite domains exactly
- **PML** — perfectly matched layer; an absorbing outer FEM region emulating open space
- **JCA** — Johnson–Champoux–Allard, a five-parameter porous material model
- **DRP** — drum reference point, the measurement location at the simulated eardrum
- **HRTF** — head-related transfer function; how a head and ears filter incoming sound
- **SOFA** — standard file format (AES69) for spatially distributed acoustic data
- **SIF** — solver input file, Elmer's case description format
- **FRD** — a plain-text frequency-response file format common in speaker design tools
```
