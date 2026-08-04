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

### 2.4 Multiple drivers — why it is not simply "run it twice and add"

Multi-driver designs are the norm, not a special case: a two-way headphone, a woofer with
a passive radiator, an in-ear with two or three balanced armatures, any loudspeaker with a
crossover. **Multiple drivers are a first-class requirement**, and three separate effects
mean they cannot be handled by simulating each driver alone and summing the results.

**They load each other through shared volumes.** If two drivers share a back volume,
driver A's cone motion pressurises the very volume that acts as driver B's suspension
stiffness. That is a genuine two-way coupling: B's motion changes A's load and vice
versa. The two must be solved *simultaneously*, as one system of equations. Superposing
two independent single-driver solutions gets this wrong, and the error is largest exactly
where the drivers overlap in frequency — the crossover region you most care about.

**They share an electrical source.** A crossover splits the input between drivers, so each
sees a different source impedance, and the amplifier sees their parallel combination
through the filter. Driver impedance is strongly frequency-dependent (it peaks at
resonance), so the crossover's actual behaviour in circuit is never the textbook filter
response computed against a fixed resistor.

**They sum as complex pressures, not powers.** At the listening point the contributions add
with magnitude *and* phase. In the crossover region the two drivers are comparable in
level, so relative phase decides whether they reinforce or cancel — a driver wired in
reverse polarity, or offset by a centimetre of path length, changes the summed response by
tens of dB. This is why crossover design is mostly phase management, and why any tool that
sums magnitudes only is useless for it.

The architectural consequence is decisive: the lumped engine must be a **general network
solver over an arbitrary topology**, not a library of closed-form enclosure formulas. Get
that right and one driver, two drivers or five are the same code path. See §6.6.

#### A caveat that matters for real designs

Lumped modelling is valid only while a cavity is small against the wavelength — the usual
criterion is one-eighth of a wavelength across its largest dimension. That limit falls
quickly with size:

| Cavity | Largest dimension | Lumped valid to |
|---|---|---|
| Ear canal / IEM front volume | ~10 mm | ~4.3 kHz |
| Small sealed earphone body | ~25 mm | ~1.7 kHz |
| Over-ear cup | ~105 mm | **~400 Hz** |
| Bookshelf loudspeaker cabinet | ~300 mm | ~140 Hz |

So for a two-way over-ear headphone, Tier 1 gives an honest account of the woofer's bass
and the crossover's *electrical* behaviour, but the tweeter's band and the acoustic
summation between drivers sit well beyond lumped validity and need Tier 2/3. The
workbench must **report this limit alongside every lumped result** rather than plotting a
confident curve to 20 kHz that stops being true above a few hundred hertz.
``AirProperties.lumped_validity_limit()`` computes it.

### 2.5 The frequencies that matter

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
| **[ngspice](https://ngspice.sourceforge.io/)** | BSD-style | Crossover networks (genuinely R/L/C), netlist import, and independent cross-check of the native solver — *not* the primary lumped engine, see §6.6 | Very mature |
| **[pyfar](https://pyfar.org/) / sofar** | MIT | Frequency-response objects, smoothing, plotting, SOFA I/O for directivity data | Active |
| **[acoupy_ears](https://gitlab.com/acoupy/acoupy_ears)** | MIT | Generates ITU-T P.57 anatomical ear canal / concha / pinna geometry as Gmsh models or STL (§6.4) | Small but sufficient |
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
| Sealed/ported box bass alignment | Lumped network (native) | < 1 s |
| Earphone response with mesh + leak, ≤ ~2 kHz | Lumped network (native) | < 1 s |
| Impedance curve of a driver, or of a multi-driver system through its crossover | Lumped network (native) | < 1 s |
| Crossover network response | Lumped network (native), or ngspice | < 1 s |
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
can *extract* them (§6.10), closing the loop.

---

## 5. Capability tiers (development order)

Each tier is independently useful. Do not start tier N+1 before tier N is validated.

### Tier 0 — Skeleton
Installable external workbench; addon-manager metadata; document object base classes with
save/restore; solver discovery and a preferences page; a "hello world" analysis that meshes
a solid and reports its volume. **Goal: the plumbing is proven before any physics.**

### Tier 1 — Lumped element modelling
No 3D solve at all. A **general nodal network solver** (§6.6) over drivers (Thiele–Small),
enclosure volumes, ports, passive radiators, acoustic resistances, leaks and crossover
components. Assemble the admittance matrix, solve per frequency, plot SPL / impedance /
cone excursion / group delay.

**Multiple drivers from the outset** — the network formulation makes one driver and five
the same code path, and retrofitting it later would mean rewriting the solver (§2.4).
Includes crossover networks, per-driver polarity, shared back volumes, and complex
summation at the observation node.

Volumes can be **measured from FreeCAD solids**, which is already more than most FOSS
tools offer. Every result reports its lumped validity limit.

Validate against closed-form sealed and vented box theory, and against a two-driver case
with a shared volume where independent superposition provably gives the wrong answer.

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

**Nothing may recompute while an object is restoring.** FreeCAD writes properties to the
file in *alphabetical* order and restores them in that order, firing `onChanged` for each,
with `Proxy` somewhere in the middle — so `onChanged` dispatches into Python well before
the object is whole. `Environment` is the worst case here: `Density` sorts first and
`Temperature` twelfth, so a guard testing only `Density` passed on the strength of the
alphabet and threw three `AttributeError`s into the Report view on every document open.
Any handler reading more than the property it was handed must call `is_restoring()` and
defer to `onDocumentRestored`, which runs once everything is in place. See `objects/base.py`.

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
│   │                                 T-S params, motor data, diaphragm ref, polarity;
│   │                                 two acoustic ports (FrontNode, BackNode) — several
│   │                                 Drivers per analysis is the normal case (§2.4)
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
├── Lumped network                  ← Tier 1; can coexist with the 3D model (§6.6)
│   ├── AcousticNode                ← a volume of air at one pressure; elements join here
│   ├── AcousticVolume              ← compliance on a node; volume auto-read from CAD
│   ├── Port                        ← mass + loss between two nodes
│   ├── PassiveRadiator             ← between two nodes
│   ├── AcousticResistance          ← damping mesh/screen between two nodes
│   └── CrossoverFilter             ← electrical filter branch feeding named drivers
├── Fixtures                        ← see §6.4 for where the geometry comes from
│   ├── EarSimulator                ← IEC 60318-1 / -4 / -5, impedance or geometry mode
│   ├── EarCanalModel               ← generated from ITU-T P.57 Type 4.3/4.4, or imported
│   ├── HeadModel                   ← imported CC-licensed head/pinna/torso scan
│   ├── InfiniteBaffle
│   └── FreeField
├── Probes
│   ├── PressureProbe               ← point mic, e.g. drum reference point
│   ├── FieldProbe                  ← plane/volume for visualisation
│   └── FarFieldSphere              ← evaluation grid for directivity
├── Study
│   ├── FrequencySweep              ← log/linear/octave-fraction, or explicit list
│   └── ParameterSweep              ← vary one named property across runs
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

### 6.4 Ear and head geometry — where it comes from

#### Do I have to put an ear in my CAD?

**No — and room acoustics is never the fallback.** Two things get conflated here, so to be
explicit:

*Room acoustics is a different problem class and is out of scope* (§1). It solves for
metres and seconds using ray tracing and statistical methods; headphone work solves for
centimetres and wave behaviour. An unclosed headphone model does not "become" a room
model. It becomes either an ill-posed problem, or — if you let the driver radiate into
open space — a *free-field* analysis, which answers "what does this cup do as a tiny
loudspeaker in open air". That is a legitimate Tier 4 question, useful for checking a
driver or studying leak-dominated behaviour, but it is not headphone response.

*The acoustic domain does have to be closed*, though, and this is what "not bounded"
meant earlier: it was a statement about geometry, not a demand for a head model. The air
volume needs a boundary everywhere, and where the ear would be there is currently nothing.

**The ear is a load, not necessarily a geometry** (§2.2). That gives three ways to close
the model, and only one of them involves ear CAD:

| Route | What you add to CAD | What you get |
|---|---|---|
| **C — impedance** | a flat **cap face** across the cup opening | SPL at the ear, with the ear represented by its acoustic input impedance. No ear geometry at all. This is what an IEC 60318-1 artificial ear *is* physically, and it is standard engineering practice |
| **A — P.57 canal** | generated canal/concha geometry | resolved canal resonances; the in-ear case |
| **B — scanned pinna/head** | a CC-licensed head mesh | pinna and concha shape, seal and leak paths, passive isolation |

Route C is the default and needs one disc-shaped face, not a head. Reach for A or B when
cavity *shape* starts to matter — above roughly 1 kHz for an over-ear cup, where the
volume stops behaving as a lumped compliance (§2.4) — or when the question is specifically
about the seal, leakage, or isolation.

#### What this means for the driver_cup model

Measured on the real assembly: the cup is a shell filling 10% of its bounding box, **open
at the ear side** (Y ≈ −1) and **closed at the back** (Y ≈ 44, an essentially full disc).
So the ear plane is well defined and a cap there is straightforward.

But capping the ear plane alone does *not* seal the volume — extraction still finds
interior and exterior connected. There are further openings: gaps between the cup, plate
and retainers, and reduced wall sections around Y ≈ 20. Acoustically that is not
necessarily a modelling defect — leakage dominates headphone bass response, and a real
cup does leak — but it means the fluid domain must be **constructed deliberately** rather
than extracted automatically. In practice: cap the ear plane, then either close the
incidental gaps or declare them as `LeakPath` elements with a measured or estimated
impedance. Deciding which gaps are real leaks and which are CAD artefacts is a judgement
the user has to make, so the extraction command must present them rather than silently
filling them.

A headphone model must terminate into something ear-shaped; "open air" is meaningless
(§2.2). There are **two distinct geometry needs**, and they have different sources:

| Need | What it's for | Source |
|---|---|---|
| **Ear canal + concha + pinna simulator** | In-ear/IEM work, occluded response, the SPL at the eardrum | ITU-T P.57 (free standard) |
| **Exterior head + pinna + torso** | Over/on-ear cup coupling, seal leakage, passive isolation, HRTF, diffraction | CC-licensed scan databases |

#### Route A — ITU-T P.57, a *freely available* standard with full geometry

This is the important find, and it changes the picture from what a first look at IEC
suggests. **ITU-T Recommendation P.57 (06/2021), "Artificial ears", is downloadable at no
cost** — the ITU made its recommendations free to all in 2007. It defines anatomically
shaped artificial ears, and its annexes give the geometry as **tabulated cross-section
coordinates in millimetres**, not just pictures:

- **Annex B — Type 4.3 artificial ear** (~32 pages): cross sections of the ear canal along
  its centreline (25 points per section, ≤1.1 mm spacing, at 0.5/2/4/6/8/10…32.5 mm), the
  concha bottom, and the pinna simulator, plus the plane positions and normals needed to
  place them in 3D.
- **Annex C — Type 4.4 artificial ear**: the same treatment for the other anatomical type.
- **Annex A**: a procedure for determining the acoustic input impedance of artificial ears
  — directly useful for the impedance route below.
- **Appendix I**: measured input impedance of artificial ears types 3.3/3.4 *and of real
  human ears*, which is validation data we would otherwise have to buy or measure.

That is a complete, loftable definition of an anatomical ear canal + concha + pinna. Type
4.3 also terminates in an IEC 60318-4-style ear simulator, so it connects the free
anatomical geometry to the standard in-ear measurement chain.

**Existing implementation:** [`acoupy_ears`](https://gitlab.com/acoupy/acoupy_ears)
(**MIT**, Stefano Tronci) already implements P.57 ear geometry in Python. It ships the
cross-section data, interpolates the section loops, and emits **Gmsh models and STL** via
`build_gmsh_model()` / `build_surface_mesh()` — which is exactly our meshing path, so
integration is a thin adapter rather than a port. Dependencies are numpy/scipy/polars/
gmsh/numpy-stl. The same author's
[computational-acoustics](https://computational-acoustics.gitlab.io/website/) project has
worked Elmer ear-canal models under CC BY 4.0, on the same solver stack we chose — useful
as a cross-check for our Tier 2/3 results.

**Redistribution posture:** the ITU asserts copyright over the *publication*. Dimensional
facts generally aren't copyrightable but table arrangement can be, so we do **not** vendor
the P.57 tables into this repo. We depend on `acoupy_ears`, or generate from the user's own
download. (Practical posture, not legal advice — worth a second look before any release.)

#### Route B — CC-licensed head and pinna scan databases

For everything outside the canal, several databases are openly licensed:

| Database | Contents | Licence |
|---|---|---|
| **[HUTUBS](https://depositonce.tu-berlin.de/items/dc2a3076-a291-417e-97f0-7697e332c960)** (TU Berlin) | 96 subjects: measured + simulated HRTFs, 3D head meshes, 25 anthropometric features, headphone transfer functions | **CC BY 4.0** |
| **[SONICOM](https://www.sonicom.eu/tools-and-resources/hrtf-dataset/)** | up to 300 subjects; ~200 with 3D scans of ears, head and torso, pre-processed for Mesh2HRTF | **CC BY** |
| **[SYMARE](http://www.morphoacoustics.org/symare-database.html)** (Sydney/York) | 61 subjects from MRI: upper torso + head + ears, meshes pre-decimated for BEM at 4/8/12/16/20 kHz | verify before use |
| **Mesh2HRTF examples** | reference head meshes shipped with the BEM solver | EUPL v1.2+ |
| **Aachen high-resolution KEMAR** | HRTFs + high-res 3D scan of the KEMAR manikin | verify before use |

HUTUBS is the best default: permissive licence, high-resolution ear scans (Artec Space
Spider for the pinnae, Kinect for the head), and cross-validated measured *and* simulated
HRTFs — so the mesh arrives with a known-good answer attached, which makes it a validation
case as well as a geometry source.

**The critical caveat: these are blocked-meatus scans.** Surface scanning cannot reach into
the ear canal, so the canal is occluded at its entrance and the meshes contain external
geometry only. That is fine for HRTF work (the canal is treated as direction-independent)
and fine for over-ear cup coupling and isolation, but it means **Route B alone cannot give
in-ear results**. Combining Route A's canal with Route B's pinna is how we get a complete
in-ear model.

#### Route C — impedance-based termination *(always available, no geometry at all)*

Represent the simulator by its acoustic input impedance as a `TransferImpedance`
termination or lumped network. Still the default for Tier 1, still the fallback when no
geometry is licensed, and still the only route that speaks directly to the IEC standards:

- **IEC 60318-1** — artificial ear, supra-aural/circumaural
- **IEC 60318-4** — occluded-ear simulator ("711 coupler"), the in-ear standard
- **IEC 60318-5** — 2 cm³ coupler, hearing-aid work

These remain paywalled and real fixtures (GRAS, B&K) remain proprietary, but their
*impedance* behaviour is published widely enough — including in P.57 Appendix I — to model
credibly.

#### Consequences for the workbench

- `EarCanalModel` gains a **generator** backed by `acoupy_ears`: pick P.57 Type 4.3 or 4.4
  and a canal length, get a solid in the document. No user CAD required.
- `EarSimulator` keeps the impedance route as its default and gains a geometry mode.
- A new **head/pinna import** helper handles the scan databases: load mesh, orient to the
  ear reference point, decimate to the target frequency, hand to BEM.
- HUTUBS becomes a **validation case** (§9): simulate its meshes, compare against its own
  measured and simulated HRTFs.

Target curves (Harman, diffuse-field, free-field) are still **loaded as user data files**,
not shipped. The workbench provides the overlay and deviation-scoring machinery.

### 6.5 Geometry input — containers, and the fluid-domain problem

**Every FreeCAD container works.** Verified against FreeCAD 1.1.1:

| Container | Exposes `Shape` | Notes |
|---|---|---|
| `Part` primitives, boolean results | yes | the simple case |
| `PartDesign::Body` | yes | one solid per body |
| `App::Link` | yes | link placement applied |
| `App::Part` container | yes | compound of children, container placement applied |
| `Assembly::AssemblyObject` | yes (when non-empty) | derives from `App::Part`; reports the *assembled* configuration |

So an earphone modelled as a multi-body assembly reads correctly, and a headphone is far
more likely to be an assembly than a single solid. Two consequences are worth stating
plainly, because both are easy to get wrong quietly.

**A child's shape is in local coordinates; a container's is global.** A part nested inside
an assembly reports its `Shape` in its own frame — a box at the assembly's `x = 50` still
reports `BoundBox.XMin = 0`. Volume is invariant under rigid transforms, so Tier 0's
measurement is correct regardless. **Positions are not.** Any face reference, mesh, probe
location or boundary condition taken from a child must be resolved through
`getGlobalPlacement()`, or it will be silently displaced by the assembly transform — a
mispositioned probe reports a plausible pressure at the wrong place. `geometry.py`
provides `global_placement_of()` for this; from Tier 2 onward every positional path must
use it.

**What gets simulated is the air, not the parts.** This is the bigger point and it is
independent of which container the user chose. A PartDesign body of an earphone shell is
the *plastic*. The acoustic domain is the cavity the plastic encloses — the front volume,
the back volume, the nozzle bore, the vent channel. So the workflow always includes a
**fluid-domain extraction** step:

1. **Model the air directly** as its own solid. Most explicit, and what experienced
   simulation users tend to do anyway.
2. **Derive it by subtraction** — a bounding solid minus the assembly's parts, giving the
   enclosed void. Convenient and the obvious candidate for automation, but fragile if the
   assembly is not watertight.
3. **Cap and close** an open cavity by adding faces across its openings, which then become
   the ports where sources, impedance terminations and leaks attach.

`AcousticDomain` therefore references *a solid representing air*, not a part. Route 2
deserves a dedicated command ("extract cavity from assembly") once meshing lands at
Tier 2; it is the step most likely to be tedious by hand and it is where an
assembly-aware workbench earns its keep over exporting STEP to a standalone tool.

**Measured on a real assembly.** Run against a two-way over-ear cup (`examples/inspect_assembly.py`,
118 solids across eight externally linked documents — a 70 mm woofer, a tweeter, cup,
plate, retainers and a PCB):

| Step | Result |
|---|---|
| Link resolution | all 8 external documents auto-load; every link exposes a placed `Shape` |
| Volume from the `Assembly::AssemblyObject` root | 125 cm³ of **material**, 118 solids |
| Fuse all solids | 7.1 s |
| Subtract from a padded bounding box | 8.9 s → 7 void regions |
| Largest void | 609 cm³ spanning the whole envelope — interior and exterior air **connected** |
| Other voids | 4 sealed pockets under 0.01 cm³ (screw holes), acoustically negligible |

Two conclusions. Boolean extraction is **fast enough to be an interactive command** — 16 s
on a full assembly is fine for something run once per geometry change. And the open cup
confirms the capping requirement is the normal case, not an edge case: the ear-side
opening leaves no closed cavity, so the acoustic domain is only defined once the ear
simulator face (or a baffle, for a loudspeaker) closes it. The extraction command must
therefore *ask* for the capping face rather than trying to infer it.

Mesh sizing for that model is undemanding: 2.15 mm elements at 20 kHz, about 49 across the
105 mm cup. The cost driver will be the thin-gap resolution of Tier 3, not the cavity.

A related consequence for Tier 3: the thin gaps that dominate earphone behaviour — the
slot around a diaphragm, a 0.1 mm vent — are exactly the features most likely to be
absent or idealised in a mechanical CAD model, since they are manufacturing clearances
rather than designed features. Extraction has to preserve them, and the workbench should
warn when a cavity contains a gap thinner than the boundary layer at the top sweep
frequency.

#### Booleans fail silently, and the failure is indistinguishable from an open model

The most expensive failure mode found so far, and the reason cavity extraction validates
its own arithmetic. A defective boundary part does not raise: it draws correctly in the 3D
view, reports `isValid()` true, has the volume it should, and quietly destroys every
boolean it takes part in.

Observed on the two-way cup. One earpad, built by a `PartDesign::AdditivePipe` sweeping two
*different* sections along a **closed** circular spine, could not close its own seam, so
OpenCascade forced it shut by inflating four seam vertices to tolerances of 8–11 mm — a
part "fuzzy" at half a millimetre against OCC's 1e-7 mm default. The consequences ran
downhill from there:

| Stage | What happened |
|---|---|
| `Shape.isValid()` | **true** — it only checks topological self-consistency |
| `Shape.check(True)` | 12× `BOPAlgo SelfIntersect`, 4× `TooSmallEdge` — never called |
| Fuse of the four boundary parts | 439.7 cm³ of input → **67.6 cm³**, reported as one valid solid |
| Subtract from the envelope | removed almost nothing; only the exterior came back |
| Verdict shown to the user | *"OPEN MODEL — no enclosed cavity. Add a cap solid across the opening."* |

The model was sealed and already capped. The tool sent its user looking for a leak that did
not exist, which is precisely the confident-wrong-answer failure §6.8 exists to prevent —
arrived at through geometry rather than through physics.

Three defences, ordered by what they cost:

1. **The union invariant, always on.** A union is never smaller than its largest part nor
   larger than the sum of them. Both bounds are exact, so this is a free and completely
   reliable trip-wire against a fuse that silently collapsed. When it trips, the extraction
   refuses to report any geometric verdict at all.
2. **A tolerance scan, always on.** Any part above `SUSPECT_TOLERANCE_MM` (1e-3 mm) is
   reported by name with its worst vertex. One micron is acoustically nothing — the viscous
   boundary layer at 1 kHz is seventy times larger — so this is purely a numerical concern,
   and the threshold sits well above the ~1e-5 mm ordinary filleted parts carry. On the case
   above this alone names the culprit in about a tenth of a second.
3. **OCC's boolean-operation check, on failure only.** `Shape.check(True)` costs roughly a
   second per detailed part — as much as the fuse itself — so it is not run speculatively.
   It runs when the invariant trips *or* when the subtraction throws, and its job is to name
   the responsible part.

The invariant is necessary but not sufficient: the same broken earpad in a different
combination fused to 263.9 cm³, which sits legitimately inside `[196.4, 439.7]`, and then
made `cut()` raise `Null shape`. So both failure paths escalate to the same named
diagnosis, and neither is allowed to emit generic advice like "try refining the selection",
which is not something a user can act on.

Two rules follow for the report text. **Never repeat advice the user has already taken** —
suggesting a cap to someone who supplied one is how the tool loses credibility on the one
message that most needed to be trusted. And **withdraw the verdict when a part is known to
be defective**: an open result is what a broken part looks like, so it is reported as
`NO VERDICT` with the part named, not as evidence about the geometry.

Findings are `Diagnostic` objects (§6.8), written to the cavity's `Diagnostics` property,
echoed to the Report view, and re-raised by the preflight pass through
`check_cavity_boundary_geometry` so a defect blocks a solve rather than merely producing a
volume of zero.

#### Generating caps from the CAD, rather than asking for them

Extraction needs a cap across every opening, and for a while the workbench asked for those
without helping to make them — which left the most-used command blocked on the most tedious
step. `AcousticCap` (`capping.py`, the **Cap opening** command) closes that gap: pick one
edge on the rim of an opening and it recovers the rest of the loop and builds the plug,
the way PartDesign's fillet expands from a single edge to the ones that continue from it.

**Capping is not sealing.** A cap closes the *fluid domain* so the boolean has something
bounded to find; it asserts nothing about whether the opening is acoustically open. A port
that has been capped here reappears in the network as a `Port` — with an
`AcousticResistance` in series if it is covered — and the cap's `OpeningArea` is precisely
the number that element needs. So the workflow is: cap **every** opening, then decide in the
network which of them are open. This is the same separation §6.7 draws between geometry and
topology, and it is the part users are most likely to misread.

**An opening is a hole in a face, not the shortest loop through an edge.** The first rule
tried was "take the shortest closed wire containing the picked edge", and it is wrong in a
way that only shows up on real parts. A 20×10 slot through a 5 mm wall has a rim of
perimeter 60, but the *side wall of its own bore* is also a closed loop through the same
edge, of perimeter 2(20+5) = 50 — shorter. Picking by length caps the inside of the bore and
leaves the mouth open. The reliable criterion is topological: the mouth of an opening is an
**inner wire** of some face, so inner wires are preferred and only within that group does
the shortest win.

**`makeOffset2D` cannot be used, and the reason is worth recording.** The cap outline is
grown slightly so it overlaps the surrounding material rather than merely touching it —
two solids meeting along a curve are the input OCC booleans handle worst. The obvious tool
for that is `Wire.makeOffset2D`, and it is a trap. `BRepOffsetAPI_MakeOffset` rejects
entirely ordinary port outlines — an 0.8 mm bored circle in the driver_cup model among them
— and it does not fail cleanly: each raised `CADKernelError` leaves the kernel slightly
worse, and a run of them **segfaults the process**. Measured, not theorised: seventeen
openings offset in sequence, each individually recoverable, killed FreeCAD on the
seventeenth, and no `try`/`except` catches that. The outline is therefore enlarged by
`Shape.scale` about its own centroid — a native uniform transform that cannot fail and,
unlike `transformGeometry`, leaves a circle a circle instead of re-approximating it as a
spline and losing a fraction of a percent of its area.

**References into an assembly must be XLinks, and must not be resolved.** Two separate
traps, both hit on the first real use. `App::PropertyLinkSubList` refuses an object it does
not own — *"does not support external object"* — and in an assembly every part is an
`App::Link` into another document, so that property type can never hold a reference the
user actually picks. It has to be `App::PropertyXLinkSubList`. Separately,
`Gui.Selection.getSelectionEx()` defaults to `resolve=1`, which walks the pick down to the
body that owns the edge, in its own document, reported as a bare `"Edge148"`. That discards
the assembly transform, so anything built from it lands where the part was *modelled*
rather than where it sits in the product. Commands taking sub-element picks use
`getSelectionEx("", 0)` and keep the full `Body004.PolarPattern001.Edge148` path, which
`resolve_reference` walks with `getSubObject` and the assembly transform applied.

**Measured on the driver_cup cup.** Enumerating the inner wires of `Body004` finds 25
mouths: the ear-side rim (79.2 cm², a 16-edge planar loop), 8 vent slots of 177.2 mm² each
seen from both ends, and 8 screw holes. Capping one mouth per opening and extracting against
the cup, plate, woofer, retainer, mount and screws yields:

| Region | Volume | What it is |
|---|---|---|
| 0 | **178.5 cm³** | air behind the diaphragm — this is `CupCavity` |
| 1 | 19.6 cm³ | air between the plate and the woofer front — ear side |
| — | 8 pockets under 0.2 cm³ | screw holes, acoustically negligible |

Probing on either side of the diaphragm confirms the two are genuinely separate, so the
driver is doing its job as a boundary. The 178.5 cm³ replaces the 200 cm³ placeholder the
headphone template ships with. The 8 slots total 14.2 cm² of vent area, which is the number
the `RearVent` `Port` wants — its 800 mm² default is a placeholder and understates the real
opening by nearly half.

### 6.6 The lumped network model

Multi-driver support (§2.4) forces the lumped engine to be a **general network solver**
rather than a set of enclosure formulas. The model is the standard electro-mechano-
acoustic analogy: every element is an impedance between two nodes, and each frequency is
one linear solve.

**Nodes are volumes of air at a common pressure.** An `AcousticNode` is a connection
point. Three kinds matter:

- an enclosed volume (a back chamber, an ear cavity), carrying a compliance to ground
- the exterior — free space or the ear simulator, the radiation termination
- an internal junction with no volume of its own, joining elements

**Elements connect nodes.** Each has a complex, frequency-dependent impedance:

| Element | Connects | Contributes |
|---|---|---|
| `Driver` | electrical terminals + **front node** + **back node** | the coupled electro-mechano-acoustic core |
| `AcousticVolume` | one node to ground | compliance |
| `Port` / `LeakPath` | two nodes | mass + resistance |
| `AcousticResistance` | two nodes | damping mesh or screen |
| `PassiveRadiator` | two nodes | mass, compliance, loss |
| `RadiationBoundary` | one node to exterior | radiation impedance |
| `CrossoverFilter` | electrical only, feeding named drivers | drive voltage and source impedance versus frequency |

The key structural point: **a driver has two acoustic ports.** Its front radiates into one
node and its back into another. That single fact is what lets the same model express a
sealed box, a vented box, a two-way sharing a back volume, and an isobaric pair — and it
is why containment-based grouping ("these drivers are inside this volume") is not enough.
Connections are explicit `App::PropertyLink` references, so an `AudioAnalysis` holding two
`Driver` objects both pointing their front node at the ear cavity produces their acoustic
summation automatically from the solve, with correct relative phase.

**Showing a graph in a tree.** A lumped network is a graph — an element joins two nodes, a
node carries many elements — so there is no single correct parent for anything, and a flat
list under the analysis loses the topology entirely. Each element is therefore filed under
the **first node it connects**, which turns the tree into an adjacency list:

```
AudioAnalysis
├── Environment
├── EarCavity
│   ├── Woofer     [EarCavity -> CupCavity]
│   ├── Tweeter    [EarCavity -> TweeterChamber]
│   └── PadSeal    [EarCavity -> exterior]
├── CupCavity
│   └── RearVent   [CupCavity -> BehindMesh]
├── BehindMesh
│   └── VentMesh   [BehindMesh -> exterior]
├── LowPass        [feeds Woofer]
└── LumpedSolver
```

The far end of every element is written into its `Label2`, which FreeCAD's tree can show
as a description column, so the choice of parent hides nothing. An `AcousticVolume` also
claims the solid it measures itself from, putting the extracted air with the acoustic
object that uses it. Every object is claimed by exactly one owner — the analysis lists
only what nothing else claims — because appearing twice would be worse than a flat list.

An element with every terminal on the exterior has no parent and stays at the top level,
where being conspicuous is the right outcome: it is almost always a wiring mistake.

**Solver choice: native, not ngspice.** Assemble the nodal admittance matrix in NumPy and
solve per frequency. Two reasons this beats generating a SPICE netlist:

1. Several impedances are not R/L/C. Radiation impedance involves Bessel functions;
   viscothermal slot impedance involves complex-argument transcendental functions
   (§2.2). Expressing those in SPICE means awkward behavioural sources or lossy
   approximations, whereas in NumPy they are one line of closed-form maths.
2. A frequency sweep is a batch of small dense complex solves — a few tens of unknowns
   across a few hundred frequencies. NumPy does that in milliseconds, vectorised, with no
   process launch, no netlist generation and no output parsing.

ngspice remains valuable for **crossover networks specifically** (where everything really
is R/L/C, and users may want to import existing netlists) and as an independent
cross-check of the native solver on cases both can express. It stays in the portfolio; it
is no longer the primary lumped engine.

**Crossovers.** A `CrossoverFilter` is one branch: it names the drivers it feeds and
supplies them with a drive voltage and a source impedance that both vary with frequency.
Nothing about the acoustic solve changes — `Driver` already had those two parameters, and
a filter simply turns them from numbers into curves. A driver with no crossover is driven
directly, so single-driver models are unaffected.

Two realisations, and the difference is not cosmetic:

* **Active** — a line-level filter with its own power amplifier per driver. The transfer
  function is exact, delay is available, and the driver stays damped by its amplifier.
* **Passive** — an L/C ladder in the signal path, synthesised from the requested alignment
  by continued-fraction expansion of the prototype rather than from a table. Its component
  values are computed against a *nominal* resistance, but the response reported is the one
  it produces into the driver's real impedance, which is not resistive. That difference is
  the reason to simulate a passive crossover at all: the ladder also sits between the
  amplifier and the coil, weakening the electrical damping term, and its own LC resonance
  is barely loaded near the driver's impedance peak. A textbook second-order low-pass into
  a real driver can peak more than 10 dB above what its transfer function alone predicts —
  which is why real passive crossovers need impedance compensation.

A filter reports three coefficients rather than a Thévenin voltage and impedance, and the
reason is worth recording. A lossless L/C ladder has *infinite* open-circuit gain at its
own resonance, and for a second-order crossover that resonance sits exactly at the
crossover frequency — the one frequency a user is certain to have in their sweep, because
it is the round number they typed. The Thévenin voltage and output impedance both diverge
there while every physical quantity stays finite. So the coil current is written directly
as `i = (V·gain − α·emf)/(α·Zc + β)`; dividing through by `α` recovers the Thévenin form,
and that is precisely the division that must never happen.

**One amplifier, several branches.** Each branch carries an independent filter. That is
exact as long as the amplifier's output impedance is zero, since it then holds the common
node at a fixed voltage regardless of what the other branch draws — true to a fraction of
a decibel for any normal damping factor. Set a non-zero source impedance and the branches
genuinely do load each other; that coupling is *not* modelled, and the checks say so.
**Two different impedance questions, and they need separate answers.**
`input_impedance(driver)` is one branch's own impedance, computed with the other drivers
*unpowered but still present* — the thing an impedance rig measures, and a property of the
branch. `system_impedance()` is the drive voltage over the total current with everything
powered: the curve the finished product presents at its plug, and what an amplifier has to
survive. They are not related by the parallel-resistor formula, because with both drivers
running each cone changes the pressure the other works against and so changes the current
it draws — the same coupling, for the same reason, that makes superposing two single-driver
models wrong.

Conflating them is not academic: a tweeter behind a high-pass, shaken through the shared
cavity by the woofer, is generating back-EMF while its own amplifier holds its terminals at
nearly zero volts. Read that as an impedance and it comes out at 0 Ω.

**Polarity is checked, because it is invisible.** An Nth-order filter rotates phase by N
quarter-turns, so at the crossover frequency the two branches are N×90° apart. At LR4 that
is a full turn and the drivers sum flat; at LR2 it is half a turn and they cancel into a
deep notch exactly where both are working hardest. Nothing in a parts list shows this, and
it sounds like a missing midrange rather than like a wiring error — so the Tier 1 checks
compare the pair's order against the drivers' `Inverted` flags and say which way round they
belong.

That rule assumes the drivers themselves are flat and in phase across the crossover
region, and real ones are not: a woofer far above its resonance and a tweeter only just
above its own each add phase of their own, and the two rotations need not cancel.
`examples/two_way_study.py` shows a plausible pair where the right answer is the opposite
of the rule. So the finding is a **warning that prompts a comparison**, not a verdict — the
rule is the correct default, an unconsidered polarity is nearly always a mistake, and the
solve is what settles it. This is the general shape guidance in §6.8 should take: state the
expectation, say why, and point at the experiment.

**Reporting obligations.** Every lumped result carries the validity limit from §2.4.
Curves are drawn beyond it only when marked — greyed, dashed, or cut off — because a
confident-looking response plotted to 20 kHz from a model valid to 400 Hz is the single
easiest way for this tool to mislead its user.

**The limit is attributed, and it is a slope rather than a cliff.** Each element answers
for the dimension that governs it — a cavity its widest span, a port its effective length
or mouth, a mesh the aperture it covers, a diaphragm its diameter, a leak its *depth*
rather than the earpad perimeter — and the narrowest wins. Piston radiation answers with
nothing, because the Bessel/Struve expression is exact at every `ka` and is not a lumped
approximation at all.

Quoting only the minimum hides too much. An over-ear analysis always expires at the cup,
which reads as though the whole model dies at 400 Hz; in the same analysis the pad seal is
valid to 10.7 kHz, the rear vent to 1.3 kHz, the tweeter chamber to 2.2 kHz. Knowing which
part binds is what turns "this is invalid" into a decision about where a 3D solve would buy
something — and says that the leak model, which dominates measured bass, was never the weak
link.

Two thresholds are reported, because the lumped error is `kL·cot(kL)`: 0.45 dB at λ/16,
2.1 dB at λ/8, 5.9 dB not far above. Plots shade three bands accordingly, the middle one
being where the answer is worth reading and worth distrusting.

A cavity's span comes from the extracted solid when one is linked. Without one it is
guessed from the volume by assuming a sphere — the most compact shape there is, hence the
most optimistic answer available, overstating a 200 cm³ cup's limit by 46% in the direction
that flatters the model. That guess is a warning, not a silent default.

### 6.7 How network objects relate to CAD geometry

A lumped network is a **topological** model, not a geometric one, so the mapping between
its objects and the parts in a document is real but partial. Being vague about that is a
good way to confuse someone who reasonably expects every object in the tree to correspond
to something they drew.

Four honest categories:

| Category | Objects | Relationship to CAD |
|---|---|---|
| **Air volumes** | `AcousticVolume` | Direct. References a solid representing the *air* and measures it. Requires the cavity to exist as a solid, which usually means extracting it (§6.5) |
| **Geometric features** | `Port`, `AcousticResistance`, `Radiation`, `LeakPath` | Parametric. Their area or width can be read from referenced faces or edges, so they track design changes |
| **Component specifications** | `Driver`, `PassiveRadiator` | None. Thiele–Small parameters come from a datasheet or a measurement rig; geometry cannot supply them |
| **Fitting and bookkeeping** | `LeakPath` gap, `AcousticNode` | None at all. A pad-to-head gap is not modelled, and an intermediate node is an artifact of the circuit representation |

Worked through for the open-back template:

| Object | In the CAD | Source of its numbers |
|---|---|---|
| `EarCavity` | the air between pad and head — *not* normally modelled | capped cavity solid, or an estimate |
| `CupCavity` | air inside the cup, minus driver, plate and PCB | cavity extraction (§6.5) |
| `Driver` | the driver part | datasheet or measurement, **not** geometry |
| `RearVent` | the actual rear openings | `AreaReference` on the opening faces; length from wall thickness |
| `VentMesh` | a mesh or fabric part, if modelled | `AreaReference`; rayls from the material spec |
| `PadSeal` | nothing — a gap that only exists when worn | `WidthReference` on the pad's contact loop; gap is a fitting parameter |
| `BehindMesh` | **nothing** | pure bookkeeping, so the vent and mesh are in series |

Two consequences worth designing around.

**Geometry references, not typed numbers, wherever the number exists in the model.**
`AcousticVolume` takes a solid; `Port`, `AcousticResistance` and `Radiation` take faces;
`LeakPath` takes an edge loop for its width. Every reference is optional, because plenty
of acoustically important quantities have no geometric counterpart. But where one does
exist, reading it means the model tracks the design instead of going stale the moment a
vent is resized.

**`AcousticNode` is the abstraction leaking, and should be minimised.** An intermediate
node exists only because two elements need to be in series; it corresponds to nothing.
Templates currently create one (`BehindMesh`) to put a mesh behind a vent. A better long
term answer is to let `Port` carry an optional built-in mesh resistance, so the common
case needs no bookkeeping node at all, and `AcousticNode` is reserved for genuine
three-way junctions.

### 6.8 Guiding the user to a correct setup

Acoustic simulation fails quietly. A driver whose back port connects to nothing, or a
sweep run an octave past lumped validity, does not crash — it returns a smooth, confident,
wrong curve, and someone new to the field has no way to tell it from a right one. A tool
aimed at people who are strong in CAD but new to audio engineering must therefore be
**opinionated and explanatory**, not a blank canvas. Five mechanisms, in the order the
user meets them.

**1. Templates, not a blank canvas.** Creating an analysis asks what is being designed —
in-ear, on-ear, over-ear closed, **over-ear open-back**, sealed box, vented box, free
field. Each instantiates a correct network topology with named nodes and placeholder
elements. The user supplies *values*; they never have to invent *topology*. Getting the
graph wrong is the most consequential and least visible mistake available, so the
workbench should not offer the chance to make it.

**2. Objects that explain themselves.** Every task panel opens with a short statement of
what the object represents physically and which results it moves. Defaults are sensible
and carry provenance — where a number came from, and how much to trust it.

**3. Preflight checks before every solve.** Implemented in `checks.py`. Each finding
answers three questions, not one: what is wrong, **why it matters physically**, and what
to do. Severity governs consequence — `ERROR` blocks the solve, `WARNING` runs but
annotates the results, `INFO` records an assumption. The catalogue grows per tier:

| Tier | Checks |
|---|---|
| 0 | medium present and singular; temperature/pressure plausible (catches Celsius-into-kelvin and the kPa trap); analysis non-empty |
| 1 | every driver has both acoustic ports connected; no floating nodes; sweep versus lumped validity; excursion beyond Xmax; crossover loads a real impedance; boundary parts fit for booleans, and a failed cavity extraction blocks the solve (§6.5) |
| 2 | fluid domain closed; unclassified openings listed for the user to call leak or artefact; mesh resolves the top frequency |
| 3 | boundary layers resolved in narrow gaps; gaps thinner than δ_v flagged; screen impedances present where geometry implies damping |
| 4 | far-field distance genuinely far field; BEM mesh adequate at the top frequency |

**4. Results that state their own limits.** Every lumped curve carries its validity limit
(§2.4), drawn greyed or cut off beyond it. A plot that runs confidently to 20 kHz from a
model valid to 400 Hz is the easiest way for this tool to mislead.

**5. Comparison as the teaching tool.** The fastest way to learn what a design choice does
is to change it and watch. `ParameterSweep` runs a study across a value and overlays the
results, so the question "what do my back vents actually do" is answered by a curve family
rather than by reading a textbook.

#### Worked example: what does an open back do?

This is a real question from the driver_cup design, whose rear openings were chosen
without analysis. It is worth walking through because it shows the guidance working, and
because — usefully — it is answerable at **Tier 1**.

*The physics.* A sealed back volume is a spring. A small one is a stiff spring: it raises
the system resonance above the driver's own free-air resonance and restricts excursion.
Opening the back removes most of that stiffness, so the resonance falls back toward the
driver's natural one. The openings also **damp** the driver: air forced through a
restricted, resistive path dissipates energy, which controls the height of the resonant
peak. That is what damping mesh over rear vents is for. Secondary effects: an open back
suppresses the internal standing waves that colour a sealed cup's midrange, and it
destroys isolation in both directions.

*The three parameters that matter*, all expressible in the Tier 1 network as elements
between the rear cavity node and the exterior:

| Parameter | Element | Effect |
|---|---|---|
| Total vent area | `Port` | how much stiffness is removed; sets the resonance shift |
| Vent depth | `Port` mass term | adds a mass that can create its own resonance |
| Mesh / screen resistance | `AcousticResistance` | damps the resonant peak; the main tuning control |

*Why Tier 1 suffices.* All of this lives at the system resonance, typically 50–500 Hz for
an over-ear headphone — comfortably inside the ~400 Hz lumped validity limit of a 105 mm
cup (§2.4). So the workbench can answer "how much should I open the back, and how much
should I damp it" with a sub-second parameter sweep, long before any 3D solve exists.

*What Tier 1 cannot answer.* Anything above a few hundred hertz: how the openings affect
midrange coloration, how much sound leaks out, how much external noise gets in, and the
crossover region where the tweeter contributes. Those need Tier 2/3 for the interior and
Tier 4 BEM for isolation. The check in mechanism 4 makes that boundary explicit on the
plot rather than leaving the user to infer it.

### 6.9 Outputs — what the user actually sees

The outputs *are* the product. Everything else is machinery for producing them.

#### The primary output is a family of curves

Almost every result is a complex quantity over frequency, held in one container
(`results/curve.py`) so plotting, smoothing, export and comparison are written once.
Values are stored **complex, always** — magnitude-only would silently destroy multi-driver
summation (§2.4) and group delay — and each curve carries the frequency above which it
stops being trustworthy.

| Curve | Read for | From tier |
|---|---|---|
| **SPL vs frequency** | the headline answer: how it sounds | 1 |
| Per-driver contributions overlaid on their sum | crossover work; where each driver hands over | 1 |
| **Electrical impedance**, magnitude and phase | amplifier matching; resonance identification | 1 |
| Sensitivity (dB/V or dB/mW) | the number that goes on a spec sheet | 1 |
| Diaphragm excursion, with an Xmax limit line | how loud it can go before distorting | 1 |
| Phase and **group delay** | timing; drivers fighting through a crossover | 1 |
| Response vs seal condition (a curve *family*) | how much bass depends on fit — dominant for real headphones | 1 |
| Deviation from a target curve | how far from Harman / diffuse-field | 1 |
| Polar plots and directivity sonograms | off-axis behaviour; loudspeaker dispersion | 4 |
| Passive isolation vs frequency | how much outside noise gets in | 4 |

#### Fields, once there is a 3D solve

Rendered through FreeCAD's existing VTK post-processing, so cut planes, contours, clipping
and animation come for free (§6.2).

| Field | Shows | From tier |
|---|---|---|
| Pressure magnitude and phase on cut planes | where standing waves sit; which cavity resonance causes which peak | 2 |
| Cavity mode shapes and their frequencies | the resonances to design away | 2 |
| Particle velocity | where air actually moves; how ports and vents load | 2 |
| **Loss density** | *where energy is dissipated* — makes a damping mesh's work visible | 3 |
| Boundary-layer resolution overlay | whether the mesh resolves the physics it claims to | 3 |
| Diaphragm structural mode shapes | break-up; which mode causes which response artefact | 4 |
| Far-field balloon / directivity sphere | radiation pattern in 3D | 4 |

The loss-density map is the one worth waiting for. Thermoviscous modelling exists because
narrow gaps dissipate energy (§2.2); seeing *which* gap is doing it turns a tuning
exercise into a design decision.

#### A summary card, not just curves

For someone strong in CAD and new to audio, a wall of curves is data, not guidance. Every
result carries a short plain-language panel with the numbers that characterise the design:

- system resonance and its Q
- −3 dB and −10 dB points — bass extension
- sensitivity, and maximum SPL before excursion exceeds Xmax
- RMS deviation from the chosen target curve, over a stated band
- the frequency above which the result is not trustworthy, stated plainly

Sensitivity is quoted **both** in dB/V and dB/mW with the impedance named, because
headphones are specified both ways and the gap is large — 15 dB at 32 Ω, 20 dB at 300 Ω —
so a figure without its units and its impedance means very little. It is quoted at 1 kHz
*or the validity limit, whichever is lower*, because the conventional 1 kHz sits well above
where a lumped model of an over-ear cup holds, and quoting there would be manufacturing a
spec-sheet number out of the part of the curve the model cannot represent. It is a property
of the product, so it appears once rather than once per driver, and is omitted entirely when
the drivers are on separate amplifiers and there is no single figure to give.

Target curves are **loaded, never shipped**: Harman, Diffuse Field and the rest are
published research but not redistributable (§6.4), so `TargetCurve` reads a CSV or FRD the
user supplies. The comparison level-matches first, because a target fixes *shape* rather
than loudness — reporting an absolute difference would make the answer depend on the drive
voltage, which has nothing to do with whether the tuning is right. And the band is stated
and clipped to the overlap of the response, the target and the validity limit, since nearly
every headphone matches nearly every target over a narrow enough range.

Scalar metrics are computed from the **trusted** portion of a curve only, so a figure like
"−3 dB at 45 Hz" is never quoted from a region the model cannot represent.

#### Comparison is the main way anyone learns

A single curve says what a design does. Two curves say what a *decision* does.
`ParameterSweep` runs a study across a value and overlays the family, with a delta view
against a chosen reference. This is how "what do my rear vents do" gets answered (§6.7),
and it is the feature most likely to change how someone designs.

Three details make it trustworthy rather than merely convenient:

* **The model is restored afterwards, including when a run fails.** A tool that quietly
  left the last swept value in place would corrupt the design it was exploring, and the
  corruption would only surface as a later solve answering a question nobody asked.
* **Swept values carry their units** — `"8 cm^2"`, not `8`. A bare number in a quantity
  field would be read in FreeCAD's internal unit, which is exactly the class of error that
  produces a plausible wrong curve rather than a crash, so it is refused.
* **A spread curve says where the parameter has any authority at all**, and the headline
  figure is taken only from within the validity limit. A parameter that moves the response
  by 8 dB at 80 Hz and 0.1 dB at 1 kHz is a bass control, and the sweep says so without
  anyone having to read five overlaid lines.

#### Provenance on everything

Every plot and export carries how it was made: solver and version, mesh size, drive level,
medium conditions, validity limit, date. A result that cannot say where it came from is
not evidence, and six months later nobody remembers which run produced which curve.

#### Export

CSV and **FRD** for curves (FRD being what loudspeaker tools read, so results can leave for
a crossover simulator), **SOFA** for directivity and HRTF data, VTU for fields, PNG/SVG for
plots, and a single-file HTML or PDF report bundling curves, summary card, provenance and
the preflight diagnostics.

### 6.10 Round-tripping between lumped and 3D

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
| Two drivers sharing a back volume | Coupled solve vs. independent superposition — they must differ, and by the amount theory predicts (§2.4) |
| Two drivers summing at one node | Complex sum; reversing one driver's polarity must produce the predicted cancellation |
| Crossover into real driver impedance | Native network solver vs. ngspice on the same netlist |
| Helmholtz resonator | Analytic + measured |
| 711-coupler input impedance | IEC 60318-4 published tolerance band |
| P.57 Type 4.3 ear canal resonances | Published Elmer models (computational-acoustics, CC BY 4.0) |
| Artificial-ear and human-ear input impedance | ITU-T P.57 Appendix I measured data |
| HRTF of a HUTUBS subject mesh | HUTUBS's own measured *and* simulated HRTFs (cross-validated) |
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
| Standardised fixture geometry is not freely available | *Largely resolved:* ITU-T P.57 is free and fully dimensioned, with an MIT implementation; CC-licensed head/pinna databases cover the exterior. Impedance-based fixtures remain the fallback (§6.4) |
| Open head scans are blocked-meatus, so they cannot model in-ear devices alone | Combine P.57 canal geometry (Route A) with scanned pinna geometry (Route B) |
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
| Gmsh | **not on PATH** — FreeCAD ships only the driver (`Mod/Fem/femmesh/gmshtools.py`), not the binary. `pip install gmsh` supplies both binary and Python API, and is already an `acoupy_ears` dependency |
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
- **EEP / ERP** — ear entrance point / ear reference point; the canal-entrance and
  reference locations used to align ear geometry between datasets and standards
- **Pinna** — the visible outer ear; **concha** — the bowl-shaped cavity leading to the canal
- **Blocked meatus** — a scan or measurement with the ear canal sealed at its entrance;
  the normal condition for optically scanned head databases
- **HRTF** — head-related transfer function; how a head and ears filter incoming sound
- **SOFA** — standard file format (AES69) for spatially distributed acoustic data
- **SIF** — solver input file, Elmer's case description format
- **FRD** — a plain-text frequency-response file format common in speaker design tools
```
