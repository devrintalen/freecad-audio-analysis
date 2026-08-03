"""Tier 1 document-object path: FreeCAD objects to a solved network.

Proves the seam works -- that a model built by clicking produces the same answer as one
built in Python, in the right units, with the right topology.
"""

from __future__ import annotations

import math

import pytest

FreeCAD = pytest.importorskip("FreeCAD")

from freecad.audio_analysis.builder import (  # noqa: E402
    BuildError,
    build_network,
    label_for_node,
    medium_of,
    sweep_frequencies,
)
from freecad.audio_analysis.objects import (  # noqa: E402
    make_analysis,
    make_environment,
    network_objects as no,
    study,
)
from freecad.audio_analysis.physics import network as net_physics  # noqa: E402


@pytest.fixture
def doc():
    document = FreeCAD.newDocument("builder_test")
    name = document.Name
    yield document
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


@pytest.fixture
def headphone(doc):
    """A sealed-front, vented-back headphone: the driver_cup topology."""
    analysis = make_analysis(doc)
    make_environment(doc, analysis)
    ear = no.make_volume(doc, analysis, "EarCavity")
    ear.Volume = FreeCAD.Units.Quantity("100 cm^3")
    cup = no.make_volume(doc, analysis, "CupCavity")
    cup.Volume = FreeCAD.Units.Quantity("200 cm^3")
    driver = no.make_driver(doc, analysis, "Woofer")
    driver.FrontNode, driver.BackNode = ear, cup
    leak = no.make_leak(doc, analysis, "PadLeak")
    leak.NodeA = ear
    vent = no.make_port(doc, analysis, "RearVent")
    vent.NodeA = cup
    vent.Area = FreeCAD.Units.Quantity("8 cm^2")
    doc.recompute()
    return analysis


class TestUnits:
    def test_volume_reaches_the_network_in_cubic_metres(self, headphone, doc):
        network, _ = build_network(headphone)
        compliance = network.element("EarCavity_compliance")
        assert compliance.volume == pytest.approx(1.0e-4)

    def test_driver_area_reaches_the_network_in_square_metres(self, headphone):
        network, _ = build_network(headphone)
        driver = network.element("Woofer")
        assert driver.parameters.Sd == pytest.approx(26.4e-4, rel=1e-6)

    def test_drive_voltage_survives_the_microvolt_internal_unit(self, headphone, doc):
        """FreeCAD stores 0.1 V as 100000 internally; the raw value must never be used."""
        driver_obj = doc.getObject("Woofer")
        driver_obj.Voltage = FreeCAD.Units.Quantity("0.5 V")
        doc.recompute()
        network, _ = build_network(headphone)
        assert network.element("Woofer").voltage == pytest.approx(0.5)

    def test_port_dimensions_reach_the_network_in_metres(self, headphone):
        network, _ = build_network(headphone)
        port = network.element("RearVent")
        assert port.area == pytest.approx(8.0e-4)
        assert port.length == pytest.approx(0.003)


class TestTopology:
    def test_nodes_and_elements_are_built(self, headphone):
        network, _ = build_network(headphone)
        assert set(network.node_names()) == {"EarCavity", "CupCavity"}
        assert len(network.drivers) == 1

    def test_driver_connects_the_named_nodes(self, headphone):
        network, _ = build_network(headphone)
        driver = network.element("Woofer")
        assert driver.front_node == "EarCavity"
        assert driver.back_node == "CupCavity"

    def test_empty_node_link_means_the_exterior(self, headphone):
        network, _ = build_network(headphone)
        # The leak and vent each have only NodeA set.
        assert network.element("PadLeak").node_b == net_physics.GROUND
        assert network.element("RearVent").node_b == net_physics.GROUND

    def test_inverted_flag_becomes_negative_polarity(self, headphone, doc):
        doc.getObject("Woofer").Inverted = True
        doc.recompute()
        network, _ = build_network(headphone)
        assert network.element("Woofer").polarity == -1

    def test_analysis_without_a_driver_is_rejected(self, doc):
        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        no.make_volume(doc, analysis, "Box")
        doc.recompute()
        with pytest.raises(BuildError, match="no Driver"):
            build_network(analysis)

    def test_label_for_node_resolves_ground(self, headphone):
        assert label_for_node(headphone, net_physics.GROUND) == "Exterior"
        assert label_for_node(headphone, "EarCavity") == "EarCavity"


class TestVolumeFromGeometry:
    def test_volume_is_measured_from_a_referenced_solid(self, doc):
        analysis = make_analysis(doc)
        box = doc.addObject("Part::Box", "Cavity")
        box.Length = box.Width = box.Height = 100.0  # 1 litre
        volume = no.make_volume(doc, analysis, "FromCad")
        volume.Shape = box
        doc.recompute()
        volume.Proxy.execute(volume)
        assert volume.Volume.getValueAs("l").Value == pytest.approx(1.0)

    def test_a_shape_without_a_solid_warns_and_keeps_the_old_value(self, doc):
        analysis = make_analysis(doc)
        sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
        volume = no.make_volume(doc, analysis, "FromSketch")
        before = volume.Volume.Value
        volume.Shape = sketch
        doc.recompute()
        volume.Proxy.execute(volume)
        assert volume.Volume.Value == pytest.approx(before)


class TestMediumAndSweep:
    def test_medium_comes_from_the_environment(self, headphone, doc):
        environment = [o for o in headphone.Group if o.Name.startswith("Environment")][0]
        environment.Temperature = FreeCAD.Units.Quantity("303.15 K")
        doc.recompute()
        assert medium_of(headphone).speed_of_sound == pytest.approx(349.4, rel=3e-3)

    def test_missing_environment_falls_back_to_room_conditions(self, doc):
        analysis = make_analysis(doc)
        assert medium_of(analysis).speed_of_sound == pytest.approx(343.4, rel=2e-3)

    def test_sweep_defaults_to_the_audio_band(self, headphone):
        f = sweep_frequencies(headphone)
        assert f.min() == pytest.approx(20.0)
        assert f.max() == pytest.approx(20000.0)

    def test_sweep_object_controls_the_frequencies(self, headphone, doc):
        sweep = study.make_frequency_sweep(doc, headphone)
        sweep.Start = FreeCAD.Units.Quantity("40 Hz")
        sweep.Stop = FreeCAD.Units.Quantity("400 Hz")
        sweep.PointsPerOctave = 12
        doc.recompute()
        f = sweep_frequencies(headphone)
        assert f.min() == pytest.approx(40.0)
        assert f.max() == pytest.approx(400.0)

    def test_two_sweeps_are_rejected(self, headphone, doc):
        study.make_frequency_sweep(doc, headphone, "SweepA")
        study.make_frequency_sweep(doc, headphone, "SweepB")
        doc.recompute()
        with pytest.raises(BuildError, match="exactly one"):
            sweep_frequencies(headphone)


class TestSolveThroughDocuments:
    def test_document_model_matches_a_hand_built_network(self, headphone, doc):
        """The seam must not change the answer."""
        from freecad.audio_analysis.physics import air
        from freecad.audio_analysis.physics.driver import DriverParameters
        from freecad.audio_analysis.results.curve import log_frequencies

        medium = air.AirProperties.at()
        frequencies = log_frequencies(20.0, 400.0, 12)

        network, _ = build_network(headphone)
        via_document = network.solve(frequencies).pressure("EarCavity")

        parameters = DriverParameters.from_thiele_small(
            fs=45.0, Re=32.0, Qms=2.5, Qes=0.7,
            Sd=26.4e-4, Vas=2.5e-3, Le=0.0, Xmax=0.8e-3, medium=medium,
        )
        hand = net_physics.Network(medium)
        hand.add(net_physics.Driver("d", parameters, "EAR", "CUP", voltage=0.1))
        hand.add(net_physics.Compliance("ear", 1.0e-4, "EAR"))
        hand.add(net_physics.Compliance("cup", 2.0e-4, "CUP"))
        hand.add(net_physics.Leak("leak", gap=1.5e-4, width=0.35, length=0.004, node_a="EAR"))
        hand.add(net_physics.AcousticMass("vent", area=8.0e-4, length=0.003, node_a="CUP"))
        via_hand = hand.solve(frequencies).pressure("EAR")

        assert via_document.spl == pytest.approx(via_hand.spl, abs=1e-9)

    def test_solver_object_runs_and_reports_status(self, headphone, doc):
        solver = study.make_lumped_solver(doc, headphone)
        solver.LargestDimension = FreeCAD.Units.Quantity("105.6 mm")
        doc.recompute()
        solution = solver.Proxy.solve(solver, headphone)
        assert "solved" in solver.Status
        assert solution.valid_below == pytest.approx(407.0, rel=1e-2)

    def test_results_are_not_persisted(self, headphone, doc, tmp_path):
        """Transient by design: re-solving is faster than risking a stale curve."""
        solver = study.make_lumped_solver(doc, headphone)
        doc.recompute()
        solver.Proxy.solve(solver, headphone)
        assert solver.Proxy.solution is not None

        # Capture the name before closing; the object reference dies with the document.
        solver_name = solver.Name
        path = str(tmp_path / "t.FCStd")
        doc.saveAs(path)
        FreeCAD.closeDocument(doc.Name)
        reopened = FreeCAD.openDocument(path)
        try:
            restored = reopened.getObject(solver_name)
            assert restored.Proxy.solution is None
            assert restored.LargestDimension is not None  # settings do persist
        finally:
            FreeCAD.closeDocument(reopened.Name)


class TestTier1Checks:
    def test_floating_node_is_reported(self, headphone, doc):
        from freecad.audio_analysis.checks import run_checks

        dangling = no.make_port(doc, headphone, "Dangling")
        node = no.make_node(doc, headphone, "Nowhere")
        dangling.NodeA = doc.getObject("CupCavity")
        dangling.NodeB = node
        doc.recompute()

        report = run_checks(headphone)
        assert any(d.code == "floating-node" for d in report.errors)

    def test_open_back_driver_is_noted_not_faulted(self, doc):
        from freecad.audio_analysis.checks import run_checks

        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        ear = no.make_volume(doc, analysis, "Ear")
        driver = no.make_driver(doc, analysis, "Drv")
        driver.FrontNode = ear
        no.make_leak(doc, analysis, "Leak").NodeA = ear
        doc.recompute()

        report = run_checks(analysis)
        assert report.can_solve
        assert any(d.code == "driver-open-back" for d in report.diagnostics)

    def test_driver_with_both_sides_open_warns(self, doc):
        from freecad.audio_analysis.checks import run_checks

        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        no.make_driver(doc, analysis, "Bare")
        doc.recompute()
        assert any(d.code == "driver-unloaded" for d in run_checks(analysis).warnings)

    def test_the_validity_limit_is_attributed_to_an_element(self, headphone, doc):
        """No solver setting is needed any more: each element supplies its own span."""
        from freecad.audio_analysis.checks import run_checks

        study.make_frequency_sweep(doc, headphone)
        study.make_lumped_solver(doc, headphone)
        doc.recompute()
        found = {d.code: d for d in run_checks(headphone).diagnostics}
        assert "validity-per-element" in found
        # The cup is the widest thing in a headphone, so it is always the constraint.
        assert "CupCavity" in found["validity-per-element"].message

    def test_a_cavity_with_no_measured_span_is_flagged(self, headphone, doc):
        """Guessing the span from volume alone assumes a sphere, which flatters the model."""
        from freecad.audio_analysis.checks import run_checks

        doc.recompute()
        assert any(d.code == "cavity-shape-assumed" for d in run_checks(headphone).warnings)

    def test_measuring_the_span_silences_it_and_lowers_the_limit(self, headphone, doc):
        from freecad.audio_analysis.checks import run_checks

        before = next(
            d for d in run_checks(headphone).diagnostics if d.code == "validity-per-element"
        )
        for label, span in (("EarCavity", "90 mm"), ("CupCavity", "105.6 mm")):
            next(o for o in headphone.Group if o.Label == label).LargestDimension = (
                FreeCAD.Units.Quantity(span)
            )
        doc.recompute()

        codes = {d.code for d in run_checks(headphone).diagnostics}
        after = next(
            d for d in run_checks(headphone).diagnostics if d.code == "validity-per-element"
        )
        assert "cavity-shape-assumed" not in codes
        assert "407 Hz" in after.message
        assert after.message != before.message


class TestSummary:
    def solved(self, analysis):
        from freecad.audio_analysis.results.curve import log_frequencies

        network, _ = build_network(analysis)
        return network.solve(log_frequencies(20.0, 400.0, 24), valid_below=407.0)

    def test_summary_mentions_every_node_and_driver(self, headphone):
        from freecad.audio_analysis.results.summary import summarise_solution

        text = summarise_solution(self.solved(headphone), headphone)
        assert "EarCavity" in text and "CupCavity" in text
        assert "excursion" in text and "impedance" in text

    def test_summary_uses_the_passband_median_by_default(self, headphone):
        from freecad.audio_analysis.results.summary import summarise_curve

        summary = summarise_curve(self.solved(headphone).pressure("EarCavity"))
        assert summary.reference_frequency is None
        assert "passband median" in summary.format()

    def test_excursion_summary_flags_exceeding_xmax(self, headphone, doc):
        from freecad.audio_analysis.results.summary import excursion_summary

        doc.getObject("Woofer").Voltage = FreeCAD.Units.Quantity("20 V")
        doc.recompute()
        solution = self.solved(headphone)
        text = excursion_summary(solution.excursion("Woofer"), 0.8e-3)
        assert "EXCEEDS Xmax" in text

    def test_summary_rejects_non_pressure_curves(self, headphone):
        from freecad.audio_analysis.results.summary import summarise_curve

        with pytest.raises(ValueError, match="pressure"):
            summarise_curve(self.solved(headphone).excursion("Woofer"))


class TestPlotting:
    def test_plot_solution_builds_a_figure(self, headphone):
        import matplotlib

        matplotlib.use("Agg")
        from freecad.audio_analysis.results.curve import log_frequencies
        from freecad.audio_analysis.results.plotting import plot_solution

        network, _ = build_network(headphone)
        solution = network.solve(log_frequencies(20.0, 2000.0, 12), valid_below=407.0)
        figure = plot_solution(solution, headphone, show=False)
        assert len(figure.axes) == 4

    def test_invalid_region_is_shaded(self, headphone):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from freecad.audio_analysis.results.curve import log_frequencies
        from freecad.audio_analysis.results.plotting import plot_curves

        network, _ = build_network(headphone)
        solution = network.solve(log_frequencies(20.0, 2000.0, 12), valid_below=407.0)
        _, axis = plt.subplots()
        plot_curves([solution.pressure("EarCavity")], axis)
        # The shaded span and the dashed limit line are both added.
        assert len(axis.patches) >= 1
        assert any(line.get_linestyle() == "--" for line in axis.lines)


class TestGeometryReferences:
    """Elements whose numbers exist in the CAD should read them from it.

    Not every acoustic element corresponds to modelled geometry -- a pad seal gap does
    not exist in CAD at all -- so references are optional everywhere and typing a number
    stays valid.
    """

    def test_port_area_from_referenced_faces(self, doc):
        analysis = make_analysis(doc)
        box = doc.addObject("Part::Box", "Vented")
        box.Length, box.Width, box.Height = 20.0, 10.0, 5.0
        doc.recompute()

        port = no.make_port(doc, analysis, "Vent")
        port.AreaReference = [(box, ("Face5",))]  # a 20 x 10 mm face
        doc.recompute()
        port.Proxy.execute(port)
        assert port.Area.getValueAs("mm^2").Value == pytest.approx(200.0)

    def test_multiple_faces_sum(self, doc):
        analysis = make_analysis(doc)
        box = doc.addObject("Part::Box", "Grille")
        box.Length, box.Width, box.Height = 20.0, 10.0, 5.0
        doc.recompute()

        port = no.make_port(doc, analysis, "Vent")
        port.AreaReference = [(box, ("Face5", "Face6"))]  # both 20 x 10 faces
        doc.recompute()
        port.Proxy.execute(port)
        assert port.Area.getValueAs("mm^2").Value == pytest.approx(400.0)

    def test_resistance_area_from_faces(self, doc):
        analysis = make_analysis(doc)
        box = doc.addObject("Part::Box", "MeshPart")
        box.Length, box.Width, box.Height = 8.0, 8.0, 1.0
        doc.recompute()

        mesh = no.make_resistance(doc, analysis, "Mesh")
        mesh.AreaReference = [(box, ("Face5",))]
        doc.recompute()
        mesh.Proxy.execute(mesh)
        assert mesh.Area.getValueAs("mm^2").Value == pytest.approx(64.0)

    def test_leak_width_from_referenced_edges(self, doc):
        analysis = make_analysis(doc)
        box = doc.addObject("Part::Box", "PadRing")
        box.Length, box.Width, box.Height = 30.0, 20.0, 5.0
        doc.recompute()

        leak = no.make_leak(doc, analysis, "Seal")
        leak.WidthReference = [(box, ("Edge1", "Edge2"))]
        doc.recompute()
        leak.Proxy.execute(leak)
        # Two edges of a 30 x 20 x 5 box; whatever they are, the sum must be positive
        # and match the shape's own edge lengths.
        expected = box.Shape.getElement("Edge1").Length + box.Shape.getElement("Edge2").Length
        assert leak.Width.getValueAs("mm").Value == pytest.approx(expected)

    def test_no_reference_leaves_the_typed_value_alone(self, doc):
        analysis = make_analysis(doc)
        port = no.make_port(doc, analysis, "Manual")
        port.Area = FreeCAD.Units.Quantity("12 cm^2")
        doc.recompute()
        port.Proxy.execute(port)
        assert port.Area.getValueAs("cm^2").Value == pytest.approx(12.0)

    def test_whole_shape_reference_uses_every_face(self, doc):
        analysis = make_analysis(doc)
        box = doc.addObject("Part::Box", "Whole")
        box.Length = box.Width = box.Height = 10.0
        doc.recompute()

        port = no.make_port(doc, analysis, "AllFaces")
        # The whole-shape form is an empty *string*: FreeCAD discards entries whose
        # sub-element tuple is empty, storing [(obj, ())] as plain [].
        port.AreaReference = [(box, "")]
        doc.recompute()
        port.Proxy.execute(port)
        assert port.Area.getValueAs("mm^2").Value == pytest.approx(600.0)  # 6 x 100

    def test_stale_reference_warns_and_keeps_the_old_value(self, doc):
        analysis = make_analysis(doc)
        box = doc.addObject("Part::Box", "Small")
        doc.recompute()
        port = no.make_port(doc, analysis, "Stale")
        port.Area = FreeCAD.Units.Quantity("5 cm^2")
        port.AreaReference = [(box, ("Face99",))]
        doc.recompute()
        port.Proxy.execute(port)
        assert port.Area.getValueAs("cm^2").Value == pytest.approx(5.0)

    def test_derived_area_reaches_the_network(self, doc):
        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        cup = no.make_volume(doc, analysis, "Cup")
        driver = no.make_driver(doc, analysis, "Drv")
        driver.BackNode = cup
        box = doc.addObject("Part::Box", "VentHole")
        box.Length, box.Width, box.Height = 40.0, 20.0, 2.0
        doc.recompute()
        port = no.make_port(doc, analysis, "Vent")
        port.NodeA = cup
        port.AreaReference = [(box, ("Face5",))]
        doc.recompute()
        port.Proxy.execute(port)

        network, _ = build_network(analysis)
        assert network.element("Vent").area == pytest.approx(800.0e-6)
