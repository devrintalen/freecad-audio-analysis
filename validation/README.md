# Validation

Benchmark cases whose answers are known **independently of this code**.

```bash
python3 validation/run.py        # all tiers
python3 validation/run.py 1      # one tier
python3 -m pytest tests/test_validation.py -q
```

## Why this is separate from `tests/`

A unit test asks whether the code does what its author meant. A benchmark asks whether
what the author meant is *true*. Acoustic simulation fails quietly — a wrong model does
not crash, it produces a smooth and confident curve — so the second question needs asking
explicitly, against an answer obtained some other way.

Every comparison names its reference and carries an explicit tolerance. "Close enough" is
never a judgement the code makes silently: the deviation is printed next to the tolerance,
and if a tolerance ever has to be widened, the widening shows up in a diff.

A reference is a closed-form solution, a published measurement, or a different solver.
It is never a previous run of this code; `tests/test_validation.py` asserts as much.

## Tier 1 — lumped network

| Case | Reference | Agreement |
|---|---|---|
| Sealed box: fc and all three Q values | Closed-form Thiele–Small alignment, Q read by the standard impedance-curve method | < 0.001% |
| Vented box: Helmholtz tuning | Analytic `fb = (c/2π)√(S/(V·L_eff))` | 0.03% |
| Rigid piston in an infinite baffle | Analytic small- and large-`ka` limits of the Bessel/Struve impedance | < 0.2% |
| Two drivers sharing a back volume | Equivalence with one driver in half the volume; predicted error from superposition | < 0.001% |
| Two drivers summing at one node | Uncoupled +6.02 dB; coupled case against hand nodal algebra; polarity cancellation | exact |
| Crossover into a real driver impedance | **ngspice**, on the same netlist | 3 ppm |
| Free-air driver impedance | **ngspice** on the standard electrical equivalent circuit | 3 ppm |

The two ngspice cases skip if ngspice is not installed.

### Notes on the probes

Several of these took more than one attempt to measure correctly, and the reasons are
worth keeping:

* **Q cannot be read from an excursion peak.** Below Q = 0.707 there is no peak, only a
  plateau. The impedance-curve method — half-power frequencies either side of the peak,
  `Qms = fc√r₀/(f₂−f₁)` — is what a bench uses and works at any Q.
* **`fb` cannot be read from the box pressure.** The driver's own acoustic impedance sits
  in parallel with the resonator and pulls the apparent peak up by about 20%. The
  impedance *minimum* between the two peaks is independent of the driver, because the load
  it sees is the box compliance in parallel with the port mass and nothing else.
* **"No acoustic load" needs a very large compliance**, not merely a large one. At 1 m³
  the residual load still shifts the resonance benchmarks by almost 1%.

## What is missing

The most important case is not here and cannot be until there is hardware in the loop:

> **A simulation stack nobody has correlated against a physical measurement is a plotting
> library.**

Everything above shows the solver agrees with theory and with another solver. None of it
shows the *model* resembles a real headphone. Correlation against a measured driver and a
measured finished headphone is the acceptance test for Tier 3 (STRUCTURE.md §9), and until
it exists every result this workbench produces should be described as unvalidated.

Tiers 2 onward add their own cases here: analytic duct modes, rectangular cavity
eigenvalues, Kirchhoff tube attenuation, coupler impedance against the IEC 60318-4
tolerance band, and HRTFs against HUTUBS's own measurements.
