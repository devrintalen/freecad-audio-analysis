"""Run the benchmark suite as part of the tests.

`validation/` holds cases whose answers are known independently of this code — closed-form
alignments, analytic radiation impedance, and ngspice on the same netlist. They are
separate from the unit tests because they answer a different question: not "does the code
do what I meant" but "is what I meant true". CLAUDE.md requires every tier to ship with
its benchmarks passing, and this is what makes that automatic rather than aspirational.

A case needing a binary this machine lacks reports skipped, which shows up here as a
pytest skip rather than a pass.
"""

from __future__ import annotations

import pytest

from validation.harness import registered


@pytest.mark.parametrize(
    "case", registered(), ids=lambda c: c.key
)
def test_benchmark(case):
    result = case.run()
    if result.skipped:
        pytest.skip(result.skipped)
    assert result.passed, "\n" + result.format()


def test_every_comparison_names_a_reference():
    """A benchmark without an independent reference is a regression test wearing a hat."""
    for case in registered():
        assert case.reference, f"{case.key} has no reference"
        assert "previous run" not in case.reference.lower()


def test_tier_one_covers_the_required_cases():
    """The Tier 1 rows of the validation table in STRUCTURE.md §9."""
    keys = {case.key for case in registered(tier=1)}
    required = {
        "sealed_box",
        "vented_box",
        "piston_radiation",
        "shared_back_volume",
        "polarity_summation",
        "crossover_vs_ngspice",
    }
    assert required <= keys, f"missing Tier 1 benchmarks: {sorted(required - keys)}"
