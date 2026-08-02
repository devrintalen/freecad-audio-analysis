# Development environment setup

Written for the Gentoo box this project is being developed on (surveyed 2026-08-02).
Adapt package names for other distributions; the structure holds.

Check the current state at any time:

```bash
python3 scripts/check_env.py
```

It reports each component with the capability tier that first needs it, so a missing
Tier 4 item is not a blocker for Tier 1 work.

---

## What is already in place

| Component | Version | Notes |
|---|---|---|
| FreeCAD | 1.1.1 | `/usr/bin/FreeCAD`, bindings at `/usr/lib64/freecad/lib64` |
| Python | 3.13.14 | **FreeCAD embeds the system interpreter** — see below, this matters |
| NumPy / SciPy | 2.4.6 / 1.17.1 | |
| matplotlib | 3.11.0 | results plotting |
| PySide6 | present | task panels |
| pytest | 9.0.3 | |
| ngspice | 43 | lumped-network solving |
| Hardware | 16 cores, 15 GB RAM | frequency sweeps parallelise across cores |

Verified working headlessly: `FreeCAD`, `femmesh.gmshtools`, `ObjectsFem`,
`femsolver.elmer.writer`, and creation of `Fem::FemAnalysis` and `Fem::FemPostPipeline`
objects. The FEM machinery we plan to wrap is functional.

**Python VTK is not installed, and that is fine.** `FemPostPipeline` is a C++ object;
field post-processing works without the Python `vtk` module. Only install `sci-libs/vtk`
with Python bindings if we later need to manipulate VTK data directly from Python.

### Why the interpreter match matters

FreeCAD 1.1.1 links `libpython3.13` — the *same* interpreter as `/usr/bin/python3`. So
anything installed into system or user site-packages is importable from inside FreeCAD
with no path juggling. On distributions where FreeCAD bundles its own Python, third-party
dependencies have to be installed into that private interpreter instead. `check_env.py`
verifies the match and warns if it ever stops holding.

---

## Stage 1 — start developing now (Tier 0 and Tier 1)

Almost nothing is missing. Tier 0 (workbench skeleton) and Tier 1 (lumped-element
modelling) need only what is already installed, plus `pip` and `pyfar`.

### 1a. Install pip

`pip` is not present, and `dev-python/pip` is in the main tree:

```bash
sudo emerge --ask dev-python/pip
```

### 1b. Decide how Python packages get installed

Gentoo marks its Python as externally managed (PEP 668), so `pip` refuses to touch
site-packages by default. Two workable routes:

**Route 1 — user site-packages (recommended for this project).**
`~/.local/lib/python3.13/site-packages` is on the default `sys.path` and user site is
enabled, so FreeCAD picks packages up automatically however it is launched — desktop
menu, terminal, anything. Nothing needs `PYTHONPATH` set.

```bash
pip install --user --break-system-packages pyfar sofar
```

The flag name is alarming but the action is not: `--user` writes only inside your home
directory and touches nothing portage owns. Reverse with `pip uninstall --user`.

**Route 2 — virtualenv with system site-packages.** Tidier if you dislike `--user`, but
FreeCAD will not see the venv unless it is launched with `PYTHONPATH` pointing at it,
which makes GUI launches awkward:

```bash
python3 -m venv --system-site-packages ~/.venvs/audio-wb
~/.venvs/audio-wb/bin/pip install pyfar sofar
# then launch FreeCAD as:
PYTHONPATH=~/.venvs/audio-wb/lib/python3.13/site-packages FreeCAD
```

Pick one and note it in the README. The rest of this document assumes Route 1.

Prefer portage where a package exists there (`dev-python/scikit-image`,
`dev-python/imageio` are in the tree); use pip only for what is not packaged
(`pyfar`, `sofar`, `polars`, `numpy-stl`, `scikit-spatial`, `acoupy_ears`).

### 1c. Verify

```bash
python3 scripts/check_env.py            # expect Tier 0/1 unblocked
PYTHONPATH=/usr/lib64/freecad/lib64 python3 -c "import FreeCAD; print(FreeCAD.Version())"
```

At this point you can build the skeleton workbench and the entire lumped-element engine.
**Do not install anything below until Tier 1 is validated** — it is a lot of compiling for
capability you cannot exercise yet.

---

## Stage 2 — 3D acoustics (Tier 2 and Tier 3)

### 2a. Gmsh

In the main tree at 4.14.1. Build it with the `python` USE flag so we get both the
`gmsh` binary (which FreeCAD's FEM workbench drives) and the Python API (which
`acoupy_ears` needs):

```bash
echo "sci-libs/gmsh python opencascade med" | sudo tee -a /etc/portage/package.use/gmsh
sudo emerge --ask sci-libs/gmsh
```

`opencascade` matters — it lets Gmsh read the STEP/BREP geometry FreeCAD exports.

Alternative if the ebuild fights you: `pip install --user --break-system-packages gmsh`
ships a prebuilt binary *and* the Python module, which is enough for both consumers.

### 2b. Elmer — the one real build

Elmer is **not in the main portage tree**. Two options:

**Option A — science overlay.** Carries `sci-misc/elmer-fem-9.0-r2`.

```bash
sudo emerge --ask app-eselect/eselect-repository
sudo eselect repository enable science
sudo emerge --sync science
sudo emerge --ask sci-misc/elmer-fem
```

Caveat: 9.0 is the last tagged upstream release (2020) and the overlay is experimental;
this ebuild has a history of link-time problems against UMFPACK. Try it first, but do not
sink hours into it.

**Option B — build from source (recommended).** Upstream development happens on the
`devel` branch, and there has been no tagged release since 9.0, so most current users
build from git. Elmer's acoustics solvers in particular benefit from post-9.0 fixes.

```bash
sudo emerge --ask sci-libs/mumps sci-libs/hypre dev-util/cmake   # optional but useful
git clone --depth 1 https://github.com/ElmerCSC/elmerfem.git ~/src/elmerfem
cmake -S ~/src/elmerfem -B ~/src/elmerfem/build \
      -DCMAKE_INSTALL_PREFIX=$HOME/.local \
      -DWITH_OpenMP=TRUE \
      -DWITH_MPI=FALSE \
      -DWITH_ELMERGUI=FALSE
cmake --build ~/src/elmerfem/build -j"$(nproc)"
cmake --install ~/src/elmerfem/build
```

Notes:
- Needs a Fortran compiler (`gcc` with `fortran` USE) plus BLAS/LAPACK.
- `WITH_MPI=FALSE` keeps the build simple. Our frequency sweeps parallelise by running
  many independent single-threaded solves, which suits 16 cores better than MPI anyway.
- Skipping ElmerGUI avoids a Qt dependency we do not need — we drive Elmer from FreeCAD.
- Installing to `~/.local` keeps it out of portage's way; ensure `~/.local/bin` is on
  `PATH`.

Verify with `ElmerSolver -v` and `ElmerGrid`, then re-run `scripts/check_env.py`.

**Before building anything on top of it**, confirm the toolchain end to end using
FreeCAD's own FEM workbench on a stock Elmer example. That separates "our SIF generator is
wrong" from "Elmer is misconfigured" — a distinction worth an hour now and a day later.

### 2c. Ear geometry (§6.4 of STRUCTURE.md)

Needs Gmsh's Python module from 2a:

```bash
pip install --user --break-system-packages git+https://gitlab.com/acoupy/acoupy_ears.git
```

Pulls in `polars`, `numpy-stl`, `scikit-spatial`, `scikit-image`, `imageio`.

Optionally download **ITU-T P.57 (06/2021)** for reference — free, no login:
<https://www.itu.int/rec/T-REC-P.57-202106-I/en>. Keep it outside the repo; we do not
redistribute it.

For exterior head geometry, fetch **HUTUBS** (CC BY 4.0) when Tier 4 starts:
<https://depositonce.tu-berlin.de/items/dc2a3076-a291-417e-97f0-7697e332c960>

---

## Stage 3 — exterior BEM (Tier 4)

**NumCalc**, from the Mesh2HRTF project (EUPL v1.2+). Small C++ program, plain Makefile:

```bash
git clone --depth 1 https://github.com/Any2HRTF/Mesh2HRTF.git ~/src/Mesh2HRTF
cd ~/src/Mesh2HRTF/mesh2hrtf/NumCalc/Source && make
cp NumCalc ~/.local/bin/
```

Optionally `pip install --user --break-system-packages mesh2hrtf` for its Python-side
mesh preparation and SOFA output helpers.

---

## Summary — the shortest path to writing code

```bash
sudo emerge --ask dev-python/pip                              # only missing Tier 0/1 piece
pip install --user --break-system-packages pyfar sofar
python3 scripts/check_env.py                                  # expect Tier 0/1 unblocked
```

Everything else can wait until the tier that needs it. Elmer is the only genuinely
time-consuming install, and Tier 1 does not touch it.
