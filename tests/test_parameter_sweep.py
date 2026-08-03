"""Parameter sweeps and result export.

The sweep is the feature that turns the workbench from a plotting tool into a design tool,
so the properties tested hardest are the ones that would make it untrustworthy: that it
restores the model afterwards even when a run fails, that a unit-less value is refused
rather than guessed at, and that the family it produces really does reflect the physics of
the parameter being varied.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

FreeCAD = pytest.importorskip("FreeCAD")

from freecad.audio_analysis.objects import (  # noqa: E402
    make_analysis,
    make_environment,
    network_objects as no,
    study,
)
from freecad.audio_analysis.objects.parameter_sweep import (  # noqa: E402
    SweepError,
    coerce,
    make_parameter_sweep,
)
from freecad.audio_analysis.results import export  # noqa: E402
from freecad.audio_analysis.results.curve import ResponseCurve, log_frequencies  # noqa: E402
from freecad.audio_analysis.results.family import CurveFamily, build_family  # noqa: E402


def q(text: str):
    return FreeCAD.Units.Quantity(text)


@pytest.fixture
def doc():
    document = FreeCAD.newDocument("sweep_test")
    name = document.Name
    yield document
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


@pytest.fixture
def vented_headphone(doc):
    """An open-back over-ear: the topology the rear-vent question is asked about."""
    analysis = make_analysis(doc)
    make_environment(doc, analysis)
    ear = no.make_volume(doc, analysis, "EarCavity")
    ear.Volume = q("100 cm^3")
    cup = no.make_volume(doc, analysis, "CupCavity")
    cup.Volume = q("200 cm^3")

    driver = no.make_driver(doc, analysis, "Driver")
    driver.FrontNode, driver.BackNode = ear, cup

    leak = no.make_leak(doc, analysis, "PadSeal")
    leak.NodeA = ear

    vent = no.make_port(doc, analysis, "RearVent")
    vent.NodeA = cup
    vent.Area = q("8 cm^2")

    sweep = study.make_frequency_sweep(doc, analysis)
    sweep.Stop = q("2000 Hz")
    solver = study.make_lumped_solver(doc, analysis)
    solver.LargestDimension = q("105 mm")
    doc.recompute()
    return analysis


def named(analysis, label):
    return next(o for o in analysis.Group if o.Label == label)


# ---------------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------------


class TestCoerce:
    def test_a_quantity_keeps_its_units(self):
        result = coerce(q("8 cm^2"), "16 cm^2")
        assert result.getValueAs("cm^2").Value == pytest.approx(16.0)

    def test_equivalent_units_are_accepted(self):
        """'1600 mm^2' and '16 cm^2' are the same area and both must work."""
        result = coerce(q("8 cm^2"), "1600 mm^2")
        assert result.getValueAs("cm^2").Value == pytest.approx(16.0)

    def test_a_bare_number_in_a_quantity_field_is_refused(self):
        """The units convention exists because a silent misread produces a plausible wrong
        curve rather than a crash."""
        with pytest.raises(SweepError, match="Write the unit out"):
            coerce(q("8 cm^2"), "16")

    def test_the_wrong_dimension_is_refused(self):
        with pytest.raises(SweepError, match="expects"):
            coerce(q("8 cm^2"), "16 mm")

    def test_floats_and_integers_pass_through(self):
        assert coerce(2.5, "20") == 20.0
        assert isinstance(coerce(2.5, "20"), float)
        assert coerce(2, "5") == 5
        assert isinstance(coerce(2, "5"), int)

    def test_booleans_accept_words(self):
        assert coerce(False, "true") is True
        assert coerce(True, "no") is False
        with pytest.raises(SweepError, match="true/false"):
            coerce(False, "maybe")

    def test_a_blank_value_is_refused(self):
        with pytest.raises(SweepError, match="blank"):
            coerce(1.0, "   ")

    def test_a_bad_number_is_refused(self):
        with pytest.raises(SweepError, match="not a number"):
            coerce(1.0, "eight")


# ---------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------


class TestValidation:
    def test_a_sweep_needs_a_target(self, doc, vented_headphone):
        sweep = make_parameter_sweep(doc, vented_headphone)
        with pytest.raises(SweepError, match="no target"):
            sweep.Proxy.validate(sweep)

    def test_a_sweep_needs_a_property_name(self, doc, vented_headphone):
        sweep = make_parameter_sweep(doc, vented_headphone)
        sweep.Target = named(vented_headphone, "RearVent")
        with pytest.raises(SweepError, match="no property"):
            sweep.Proxy.validate(sweep)

    def test_a_misspelt_property_is_caught_by_name(self, doc, vented_headphone):
        sweep = make_parameter_sweep(doc, vented_headphone)
        sweep.Target, sweep.Property = named(vented_headphone, "RearVent"), "area"
        sweep.Values = ["4 cm^2", "8 cm^2"]
        with pytest.raises(SweepError, match="case-sensitive"):
            sweep.Proxy.validate(sweep)

    def test_one_value_is_not_a_sweep(self, doc, vented_headphone):
        sweep = make_parameter_sweep(doc, vented_headphone)
        sweep.Target, sweep.Property = named(vented_headphone, "RearVent"), "Area"
        sweep.Values = ["8 cm^2"]
        with pytest.raises(SweepError, match="at least two"):
            sweep.Proxy.validate(sweep)


# ---------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------


class TestRunning:
    def build(self, doc, analysis, target, prop, values, observe=None):
        sweep = make_parameter_sweep(doc, analysis)
        sweep.Target, sweep.Property, sweep.Values = named(analysis, target), prop, values
        if observe is not None:
            sweep.Observe = named(analysis, observe)
        doc.recompute()
        return sweep

    def test_a_sweep_produces_one_curve_per_value(self, doc, vented_headphone):
        sweep = self.build(
            doc, vented_headphone, "RearVent", "Area", ["2 cm^2", "8 cm^2", "32 cm^2"]
        )
        family = sweep.Proxy.run(sweep, vented_headphone)
        assert len(family) == 3
        assert family.labels == ["2 cm^2", "8 cm^2", "32 cm^2"]

    def test_the_model_is_left_exactly_as_it_was(self, doc, vented_headphone):
        """A design tool that quietly leaves the last swept value in place would corrupt
        the model it was meant to explore."""
        vent = named(vented_headphone, "RearVent")
        before = vent.Area.getValueAs("cm^2").Value
        sweep = self.build(doc, vented_headphone, "RearVent", "Area", ["2 cm^2", "32 cm^2"])
        sweep.Proxy.run(sweep, vented_headphone)
        assert vent.Area.getValueAs("cm^2").Value == pytest.approx(before)

    def test_the_model_is_restored_even_when_a_run_fails(self, doc, vented_headphone):
        vent = named(vented_headphone, "RearVent")
        before = vent.Area.getValueAs("cm^2").Value
        sweep = self.build(
            doc, vented_headphone, "RearVent", "Area", ["2 cm^2", "not an area", "32 cm^2"]
        )
        with pytest.raises(SweepError):
            sweep.Proxy.run(sweep, vented_headphone)
        assert vent.Area.getValueAs("cm^2").Value == pytest.approx(before)

    def test_the_observed_node_defaults_to_the_first_volume(self, doc, vented_headphone):
        """For a headphone that is the ear cavity, which is where the answer lives."""
        sweep = self.build(doc, vented_headphone, "RearVent", "Area", ["2 cm^2", "32 cm^2"])
        family = sweep.Proxy.run(sweep, vented_headphone)
        assert family.metadata["observed"] == named(vented_headphone, "EarCavity").Name

    def test_the_observed_node_can_be_chosen(self, doc, vented_headphone):
        sweep = self.build(
            doc, vented_headphone, "RearVent", "Area", ["2 cm^2", "32 cm^2"], observe="CupCavity"
        )
        family = sweep.Proxy.run(sweep, vented_headphone)
        assert family.metadata["observed"] == named(vented_headphone, "CupCavity").Name

    def test_curves_carry_the_validity_limit(self, doc, vented_headphone):
        sweep = self.build(doc, vented_headphone, "RearVent", "Area", ["2 cm^2", "32 cm^2"])
        family = sweep.Proxy.run(sweep, vented_headphone)
        assert family.valid_below == pytest.approx(409.0, abs=2.0)

    def test_status_records_where_the_parameter_has_authority(self, doc, vented_headphone):
        sweep = self.build(doc, vented_headphone, "RearVent", "Area", ["2 cm^2", "32 cm^2"])
        sweep.Proxy.run(sweep, vented_headphone)
        assert "dB peak spread" in sweep.Status

    def test_vent_area_acts_at_the_helmholtz_resonance_not_in_the_deep_bass(
        self, doc, vented_headphone
    ):
        """A result worth having the tool tell you, because it is counter-intuitive.

        An undamped vent is a pure acoustic mass, and a mass is a *short* at low
        frequency: however small the opening, the cup is connected to ambient at 30 Hz, so
        changing its area barely moves the bass at all. Where area does matter is around
        the resonance the vent mass forms with the cup compliance, a few hundred hertz up.
        Sizing rear openings by their effect on bass would be sizing them by the one thing
        they do not control -- that is the mesh's job, tested below.
        """
        sweep = self.build(
            doc, vented_headphone, "RearVent", "Area", ["0.5 cm^2", "8 cm^2", "50 cm^2"]
        )
        family = sweep.Proxy.run(sweep, vented_headphone)
        spread = family.spread()

        assert spread[np.argmin(np.abs(family.frequency - 30.0))] < 0.5
        assert spread.max() > 3.0
        assert family.most_sensitive_frequency() > 100.0

    def test_damping_a_vent_seals_the_back_and_costs_bass(self, doc, vented_headphone):
        """The main tuning control for an open back, per STRUCTURE.md §6.8.

        Raising the mesh resistance progressively closes the rear path. A closed cup is a
        spring behind the diaphragm, and that spring stiffens the system, raises its
        resonance and cuts output below it. So more damping means less bass -- not, as one
        might guess, a more damped bass peak.
        """
        behind = no.make_node(doc, vented_headphone, "BehindMesh")
        vent = named(vented_headphone, "RearVent")
        vent.NodeB = behind
        mesh = no.make_resistance(doc, vented_headphone, "VentMesh")
        mesh.NodeA, mesh.Area = behind, q("8 cm^2")
        doc.recompute()

        sweep = self.build(
            doc, vented_headphone, "VentMesh", "SpecificResistance", ["1", "20", "200", "2000"]
        )
        family = sweep.Proxy.run(sweep, vented_headphone)
        at_30 = [c.spl[np.argmin(np.abs(family.frequency - 30.0))] for c in family.curves]
        assert at_30 == sorted(at_30, reverse=True), "more resistance must mean less bass"
        assert at_30[0] - at_30[-1] > 5.0

    def test_a_reference_of_minus_one_means_no_baseline(self, doc, vented_headphone):
        sweep = self.build(doc, vented_headphone, "RearVent", "Area", ["2 cm^2", "32 cm^2"])
        sweep.Reference = -1
        family = sweep.Proxy.run(sweep, vented_headphone)
        assert family.baseline() is None


# ---------------------------------------------------------------------------------
# The family container
# ---------------------------------------------------------------------------------


def flat_curve(level: float, label: str) -> ResponseCurve:
    frequency = log_frequencies(20.0, 2000.0, 6)
    return ResponseCurve(
        frequency, np.full(frequency.shape, level, dtype=complex), label=label, valid_below=400.0
    )


class TestCurveFamily:
    def test_deltas_are_relative_to_the_reference(self):
        family = build_family(
            "x", [("a", flat_curve(1.0, "a")), ("b", flat_curve(2.0, "b"))], reference=0
        )
        deltas = family.deltas()
        assert np.allclose(deltas[0], 0.0)
        assert np.allclose(deltas[1], 6.0206, atol=1e-3)

    def test_deltas_need_a_reference(self):
        family = build_family("x", [("a", flat_curve(1.0, "a"))], reference=None)
        with pytest.raises(ValueError, match="no reference"):
            family.deltas()

    def test_spread_is_the_peak_to_peak_variation(self):
        family = build_family(
            "x",
            [("a", flat_curve(1.0, "a")), ("b", flat_curve(2.0, "b")), ("c", flat_curve(4.0, "c"))],
        )
        assert np.allclose(family.spread(), 12.0412, atol=1e-3)

    def test_the_family_inherits_the_strictest_validity_limit(self):
        loose = flat_curve(1.0, "a")
        strict = ResponseCurve(
            loose.frequency, loose.values, label="b", valid_below=100.0
        )
        family = build_family("x", [("a", loose), ("b", strict)])
        assert family.valid_below == 100.0

    def test_mismatched_frequency_axes_are_refused(self):
        other = ResponseCurve(
            log_frequencies(20.0, 2000.0, 12),
            np.ones(log_frequencies(20.0, 2000.0, 12).shape, dtype=complex),
        )
        with pytest.raises(ValueError, match="one frequency axis"):
            CurveFamily("x", ["a", "b"], [flat_curve(1.0, "a"), other])

    def test_labels_must_match_the_curves(self):
        with pytest.raises(ValueError, match="must match"):
            CurveFamily("x", ["a"], [flat_curve(1.0, "a"), flat_curve(2.0, "b")])

    def test_an_out_of_range_reference_is_refused(self):
        with pytest.raises(ValueError, match="outside"):
            CurveFamily("x", ["a"], [flat_curve(1.0, "a")], reference=5)

    def test_the_most_sensitive_frequency_stays_inside_the_trusted_range(self):
        """Otherwise a sweep would report its headline number from a region the model
        cannot represent."""
        frequency = log_frequencies(20.0, 2000.0, 6)
        quiet = np.ones(frequency.shape, dtype=complex)
        loud = quiet.copy()
        loud[frequency > 1000.0] = 100.0  # a huge difference, but above the limit
        family = build_family(
            "x",
            [
                ("a", ResponseCurve(frequency, quiet, valid_below=400.0)),
                ("b", ResponseCurve(frequency, loud, valid_below=400.0)),
            ],
        )
        assert family.most_sensitive_frequency() <= 400.0

    def test_summarise_calls_out_a_parameter_that_does_nothing(self):
        family = build_family(
            "x", [("a", flat_curve(1.0, "a")), ("b", flat_curve(1.0, "b"))]
        )
        assert "barely moves the result" in family.summarise()

    def test_to_csv_writes_one_column_per_run(self, tmp_path):
        family = build_family(
            "RearVent.Area", [("2 cm^2", flat_curve(1.0, "a")), ("8 cm^2", flat_curve(2.0, "b"))]
        )
        path = str(tmp_path / "family.csv")
        family.to_csv(path)
        text = open(path, encoding="utf-8").read().splitlines()
        header = next(line for line in text if line.startswith("frequency_Hz"))
        assert "SPL_dB [2 cm^2]" in header and "SPL_dB [8 cm^2]" in header
        assert any(line.startswith("# valid below") for line in text)
        # Two comment lines (parameter, validity) plus a column header, then the data.
        assert len(text) - 3 == family.frequency.size


# ---------------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------------


class TestExport:
    @pytest.fixture
    def solved(self, vented_headphone):
        from freecad.audio_analysis.builder import build_network, sweep_frequencies

        network, _ = build_network(vented_headphone)
        return network.solve(sweep_frequencies(vented_headphone), valid_below=409.0)

    def test_a_solve_exports_pressure_impedance_and_excursion(
        self, tmp_path, solved, vented_headphone
    ):
        written = export.export_all(str(tmp_path), solved, analysis=vented_headphone)
        names = {os.path.basename(p) for p in written}
        assert "pressure_EarCavity.csv" in names
        assert "pressure_EarCavity.frd" in names
        assert "impedance_Driver.csv" in names
        assert "excursion_Driver.csv" in names

    def test_only_pressure_curves_get_an_frd(self, tmp_path, solved, vented_headphone):
        written = export.export_all(str(tmp_path), solved, analysis=vented_headphone)
        frds = [p for p in written if p.endswith(".frd")]
        assert frds and all("pressure_" in os.path.basename(p) for p in frds)

    def test_exports_carry_their_validity_limit(self, tmp_path, solved, vented_headphone):
        export.export_all(str(tmp_path), solved, analysis=vented_headphone)
        text = open(tmp_path / "pressure_EarCavity.csv", encoding="utf-8").read()
        assert "valid below: 409.0 Hz" in text

    def test_a_single_driver_gets_no_system_impedance_file(
        self, tmp_path, solved, vented_headphone
    ):
        written = export.export_all(str(tmp_path), solved, analysis=vented_headphone)
        assert not any("impedance_system" in p for p in written)

    def test_a_family_exports_as_one_wide_csv(self, tmp_path):
        family = build_family(
            "RearVent.Area", [("2 cm^2", flat_curve(1.0, "a")), ("8 cm^2", flat_curve(2.0, "b"))]
        )
        written = export.export_all(str(tmp_path), families=[family])
        assert [os.path.basename(p) for p in written] == ["sweep_RearVent.Area.csv"]

    def test_a_missing_directory_is_refused_before_anything_is_written(self, tmp_path, solved):
        missing = str(tmp_path / "nope")
        with pytest.raises(ValueError, match="not a directory"):
            export.export_all(missing, solved)
        assert not os.path.exists(missing)

    def test_labels_with_awkward_characters_become_safe_file_names(self):
        assert export.safe_name("Ear cavity / left") == "Ear_cavity_left"
        assert export.safe_name("   ") == "result"


# ---------------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------------


class TestPlotting:
    @pytest.fixture(autouse=True)
    def headless_backend(self):
        matplotlib = pytest.importorskip("matplotlib")
        matplotlib.use("Agg")

    def test_a_family_with_a_reference_gets_a_delta_panel(self):
        from freecad.audio_analysis.results.plotting import plot_family

        family = build_family(
            "x", [("a", flat_curve(1.0, "a")), ("b", flat_curve(2.0, "b"))], reference=0
        )
        figure = plot_family(family, show=False)
        assert len(figure.axes) == 2

    def test_a_family_without_a_reference_gets_only_the_overlay(self):
        from freecad.audio_analysis.results.plotting import plot_family

        family = build_family(
            "x", [("a", flat_curve(1.0, "a")), ("b", flat_curve(2.0, "b"))], reference=None
        )
        figure = plot_family(family, show=False)
        assert len(figure.axes) == 1
