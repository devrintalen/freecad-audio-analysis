"""Template tests.

A template's whole purpose is to hand the user a *correct* topology, so the bar is that
every one of them builds, passes its own preflight checks with no errors, and solves.
"""

from __future__ import annotations

import math

import pytest

FreeCAD = pytest.importorskip("FreeCAD")

from freecad.audio_analysis.builder import build_network, sweep_frequencies  # noqa: E402
from freecad.audio_analysis.checks import run_checks  # noqa: E402
from freecad.audio_analysis.objects import make_analysis, make_environment  # noqa: E402
from freecad.audio_analysis.templates import (  # noqa: E402
    TEMPLATES,
    TEMPLATES_BY_KEY,
    apply_template,
)

ALL_KEYS = [t.key for t in TEMPLATES]


@pytest.fixture
def doc():
    document = FreeCAD.newDocument("template_test")
    name = document.Name
    yield document
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


def built(doc, key):
    analysis = make_analysis(doc)
    make_environment(doc, analysis)
    apply_template(key, doc, analysis)
    return analysis


@pytest.mark.parametrize("key", ALL_KEYS)
class TestEveryTemplate:
    def test_builds_without_errors(self, doc, key):
        analysis = built(doc, key)
        report = run_checks(analysis)
        assert report.can_solve, report.format()

    def test_produces_no_unexpected_warnings(self, doc, key):
        """A template that warns about its own wiring has not done its job.

        The two validity warnings are excepted and expected. Templates deliberately sweep
        the whole audio band rather than stopping at the limit, because seeing the upper
        range marked as untrustworthy is more useful than not seeing it at all -- and for
        the same reason the two-way template puts its crossover where a real one goes,
        which is above where a lumped model of an over-ear cup holds.
        """
        expected = {"beyond-lumped-validity", "crossover-beyond-validity"}
        analysis = built(doc, key)
        warnings = [w for w in run_checks(analysis).warnings if w.code not in expected]
        assert warnings == [], "\n".join(w.format() for w in warnings)

    def test_reports_where_its_results_stop_being_valid(self, doc, key):
        analysis = built(doc, key)
        codes = {d.code for d in run_checks(analysis).diagnostics}
        assert "validity-unknown" not in codes, "every template must set LargestDimension"

    def test_solves(self, doc, key):
        analysis = built(doc, key)
        network, _ = build_network(analysis)
        solution = network.solve(sweep_frequencies(analysis))
        assert solution.frequency.size > 0

    def test_has_a_driver_and_a_solver(self, doc, key):
        analysis = built(doc, key)
        network, _ = build_network(analysis)
        assert len(network.drivers) >= 1

    def test_no_floating_nodes(self, doc, key):
        analysis = built(doc, key)
        network, _ = build_network(analysis)
        assert network.floating_nodes() == []

    def test_describes_its_next_steps(self, doc, key):
        template = TEMPLATES_BY_KEY[key]
        assert template.summary and template.next_steps


class TestHeadphoneTemplates:
    def test_open_back_has_a_vent_in_series_with_a_mesh(self, doc):
        """The series wiring is the whole point; parallel would be a different model."""
        analysis = built(doc, "over_ear_open")
        network, _ = build_network(analysis)
        vent = network.element("RearVent")
        mesh = network.element("VentMesh")
        # They share the intermediate node, and only the mesh reaches the exterior.
        assert vent.node_b == mesh.node_a
        assert mesh.node_b == "GROUND"
        assert vent.node_a == "CupCavity"

    def test_closed_back_has_no_vent(self, doc):
        analysis = built(doc, "over_ear_closed")
        network, _ = build_network(analysis)
        with pytest.raises(KeyError):
            network.element("RearVent")

    def test_open_back_gives_more_bass_than_closed(self):
        """The physical result the templates exist to let someone discover.

        Each template gets its own document: building both in one would suffix the
        second's object names to EarCavity001 and the lookup would miss.
        """
        from freecad.audio_analysis.results.curve import log_frequencies

        frequencies = log_frequencies(20.0, 200.0, 12)
        levels = {}
        for key in ("over_ear_open", "over_ear_closed"):
            document = FreeCAD.newDocument(f"cmp_{key}")
            try:
                analysis = built(document, key)
                network, _ = build_network(analysis)
                levels[key] = network.solve(frequencies).pressure("EarCavity").spl_at(30.0)
            finally:
                FreeCAD.closeDocument(document.Name)
        assert levels["over_ear_open"] > levels["over_ear_closed"] + 3.0

    def test_driver_front_and_back_are_distinct_named_volumes(self, doc):
        analysis = built(doc, "over_ear_open")
        network, _ = build_network(analysis)
        driver = network.drivers[0]
        assert driver.front_node == "EarCavity"
        assert driver.back_node == "CupCavity"

    def test_in_ear_canal_is_smaller_than_an_over_ear_cavity(self, doc):
        in_ear = built(doc, "in_ear")
        network, _ = build_network(in_ear)
        assert network.element("EarCanal_compliance").volume < 5e-6


class TestLoudspeakerTemplates:
    def test_sealed_box_leaves_the_front_open_to_the_room(self, doc):
        analysis = built(doc, "sealed_box")
        network, _ = build_network(analysis)
        driver = network.drivers[0]
        assert driver.front_node == "GROUND"
        assert driver.back_node == "BoxVolume"

    def test_far_field_pressure_is_computable(self, doc):
        from freecad.audio_analysis.results.curve import log_frequencies

        analysis = built(doc, "sealed_box")
        network, _ = build_network(analysis)
        solution = network.solve(log_frequencies(20.0, 500.0, 12))
        curve = solution.far_field_pressure([network.drivers[0].name], distance=1.0)
        # A 133 cm^2 woofer at 2.83 V should land in the usual 80-95 dB region at 1 m.
        assert 75.0 < curve.spl_at(200.0) < 100.0

    def test_far_field_falls_with_distance(self, doc):
        from freecad.audio_analysis.results.curve import log_frequencies

        analysis = built(doc, "sealed_box")
        network, _ = build_network(analysis)
        solution = network.solve(log_frequencies(20.0, 500.0, 12))
        near = solution.far_field_pressure(["Driver"], 1.0).spl_at(200.0)
        far = solution.far_field_pressure(["Driver"], 2.0).spl_at(200.0)
        assert near - far == pytest.approx(6.0206, abs=1e-3)  # inverse square law

    def test_half_space_is_6db_louder_than_full(self, doc):
        from freecad.audio_analysis.results.curve import log_frequencies

        analysis = built(doc, "sealed_box")
        network, _ = build_network(analysis)
        solution = network.solve(log_frequencies(20.0, 500.0, 12))
        half = solution.far_field_pressure(["Driver"], 1.0, half_space=True).spl_at(200.0)
        full = solution.far_field_pressure(["Driver"], 1.0, half_space=False).spl_at(200.0)
        # Exactly 20*log10(2); the rounded 6.0206 is not equal to it at 1e-9.
        assert half - full == pytest.approx(20.0 * math.log10(2.0), abs=1e-9)

    def test_vented_box_adds_a_port(self, doc):
        analysis = built(doc, "vented_box")
        network, _ = build_network(analysis)
        assert network.element("Port").node_a == "BoxVolume"
        assert network.element("Port").node_b == "GROUND"

    def test_far_field_rejects_empty_sources(self, doc):
        from freecad.audio_analysis.results.curve import log_frequencies

        analysis = built(doc, "sealed_box")
        network, _ = build_network(analysis)
        solution = network.solve(log_frequencies(20.0, 500.0, 12))
        with pytest.raises(ValueError, match="no radiating"):
            solution.far_field_pressure([])
        with pytest.raises(ValueError, match="distance"):
            solution.far_field_pressure(["Driver"], distance=0.0)


class TestTemplateRegistry:
    def test_unknown_key_raises_and_lists_options(self, doc):
        analysis = make_analysis(doc)
        with pytest.raises(KeyError, match="over_ear_open"):
            apply_template("nonsense", doc, analysis)

    def test_keys_are_unique(self):
        assert len(TEMPLATES_BY_KEY) == len(TEMPLATES)
