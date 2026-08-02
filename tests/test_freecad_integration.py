"""Integration tests that need a real FreeCAD.

These run inside a FreeCAD-enabled interpreter. ``conftest.py`` skips them when FreeCAD
cannot be imported, so the pure-physics suite still runs anywhere.

What is being proven here is the Tier 0 claim: the plumbing works. Objects are created,
properties round-trip through a saved file, derived values recompute, geometry is read,
and units come out right on the far side.
"""

from __future__ import annotations

import pytest

FreeCAD = pytest.importorskip("FreeCAD")

from freecad.audio_analysis.geometry import NoSolidError, measure_volume, measure_volumes
from freecad.audio_analysis.objects import (
    find_active_analysis,
    is_audio_object,
    make_analysis,
    make_environment,
)
from freecad.audio_analysis.objects.analysis import AudioAnalysis
from freecad.audio_analysis.objects.environment import Environment
from freecad.audio_analysis.physics import air


@pytest.fixture
def doc():
    """A scratch FreeCAD document, closed afterwards.

    The name is captured up front and closing tolerates absence, because tests that
    exercise save/reload close the document themselves.
    """
    document = FreeCAD.newDocument("audio_test")
    name = document.Name
    yield document
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


class TestAnalysis:
    def test_creates_analysis(self, doc):
        analysis = make_analysis(doc)
        assert is_audio_object(analysis, AudioAnalysis.Type)
        assert analysis.Proxy.Type == "Audio::Analysis"

    def test_environment_joins_the_analysis_group(self, doc):
        analysis = make_analysis(doc)
        env = make_environment(doc, analysis)
        assert env in analysis.Group

    def test_find_active_analysis_with_exactly_one(self, doc):
        analysis = make_analysis(doc)
        assert find_active_analysis(doc) is analysis

    def test_find_active_analysis_is_none_when_ambiguous(self, doc):
        make_analysis(doc)
        make_analysis(doc)
        # Two candidates and no GUI selection: refuse to guess.
        assert find_active_analysis(doc) is None

    def test_find_active_analysis_is_none_when_empty(self, doc):
        assert find_active_analysis(doc) is None


class TestEnvironment:
    def test_defaults_are_room_conditions(self, doc):
        env = make_environment(doc)
        assert env.Temperature.getValueAs("K").Value == pytest.approx(293.15)
        assert env.StaticPressure.getValueAs("Pa").Value == pytest.approx(101325.0)
        assert env.RelativeHumidity == 50

    def test_derived_values_are_populated(self, doc):
        env = make_environment(doc)
        assert env.SpeedOfSound == pytest.approx(343.4, rel=2e-3)
        assert env.Density == pytest.approx(1.199, rel=2e-3)
        assert env.CharacteristicImpedance == pytest.approx(413.0, rel=1e-2)
        assert env.PrandtlNumber == pytest.approx(0.71, rel=2e-2)

    def test_boundary_layer_reported_in_micrometres(self, doc):
        # ~69 um at 1 kHz. Reported in um because metres would read as 6.9e-05.
        env = make_environment(doc)
        assert env.ViscousBoundaryLayer1kHz == pytest.approx(69.0, rel=5e-2)

    def test_changing_temperature_recomputes_speed_of_sound(self, doc):
        env = make_environment(doc)
        before = env.SpeedOfSound
        env.Temperature = FreeCAD.Units.Quantity("303.15 K")  # 30 C
        doc.recompute()
        assert env.SpeedOfSound > before
        assert env.SpeedOfSound == pytest.approx(349.4, rel=3e-3)

    def test_pressure_unit_conversion_is_correct(self, doc):
        """FreeCAD stores pressure internally in kPa; the conversion must not slip."""
        env = make_environment(doc)
        # Raw internal value is kilopascals...
        assert env.StaticPressure.Value == pytest.approx(101.325)
        # ...but the physics must see pascals.
        props = env.Proxy.air_properties(env)
        assert props.pressure == pytest.approx(101325.0)
        assert props.density == pytest.approx(1.199, rel=2e-3)

    def test_derived_properties_are_read_only_in_the_editor(self, doc):
        env = make_environment(doc)
        assert env.getEditorMode("Density") == ["ReadOnly"]
        assert env.getEditorMode("Temperature") == []

    def test_matches_the_pure_physics_module(self, doc):
        env = make_environment(doc)
        expected = air.AirProperties.at(293.15, 101325.0, 0.5)
        assert env.SpeedOfSound == pytest.approx(expected.speed_of_sound)
        assert env.Density == pytest.approx(expected.density)


class TestPersistence:
    def test_objects_survive_save_and_reload(self, doc, tmp_path):
        analysis = make_analysis(doc)
        env = make_environment(doc, analysis)
        env.Temperature = FreeCAD.Units.Quantity("310.15 K")  # 37 C, body temperature
        env.RelativeHumidity = 90  # in-ear conditions
        doc.recompute()
        expected_c = env.SpeedOfSound

        path = str(tmp_path / "persisted.FCStd")
        doc.saveAs(path)
        FreeCAD.closeDocument(doc.Name)

        reloaded = FreeCAD.openDocument(path)
        try:
            restored = [o for o in reloaded.Objects if is_audio_object(o, Environment.Type)]
            assert len(restored) == 1
            env2 = restored[0]
            assert env2.RelativeHumidity == 90
            assert env2.Temperature.getValueAs("K").Value == pytest.approx(310.15)
            assert env2.SpeedOfSound == pytest.approx(expected_c)
            # The proxy must come back alive, not as a bare dict.
            assert env2.Proxy.Type == Environment.Type
            assert hasattr(env2.Proxy, "air_properties")
        finally:
            FreeCAD.closeDocument(reloaded.Name)

    def test_schema_version_is_recorded(self, doc):
        env = make_environment(doc)
        assert env.SchemaVersion >= 1

    def test_ensure_properties_is_idempotent(self, doc):
        env = make_environment(doc)
        before = set(env.PropertiesList)
        env.Proxy.ensure_properties(env)
        env.Proxy.ensure_properties(env)
        assert set(env.PropertiesList) == before

    def test_restore_adds_properties_missing_from_an_older_file(self, doc):
        """Simulate a file written before a property existed."""
        env = make_environment(doc)
        env.removeProperty("PrandtlNumber")
        assert not hasattr(env, "PrandtlNumber")
        env.Proxy.onDocumentRestored(env)
        assert hasattr(env, "PrandtlNumber")


class TestGeometry:
    def test_measures_a_box(self, doc):
        # 100 x 100 x 100 mm == 1 litre exactly.
        box = doc.addObject("Part::Box", "Box")
        box.Length = box.Width = box.Height = 100.0
        doc.recompute()

        measurement = measure_volume(box)
        assert measurement.volume_mm3 == pytest.approx(1.0e6)
        assert measurement.volume_litres == pytest.approx(1.0)
        assert measurement.volume_m3 == pytest.approx(0.001)
        assert measurement.solid_count == 1

    def test_five_litre_enclosure(self, doc):
        box = doc.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 200.0, 250.0, 100.0
        doc.recompute()
        assert measure_volume(box).volume_litres == pytest.approx(5.0)

    def test_small_cavity_reported_in_cubic_centimetres(self, doc):
        # A 10 mm cube is 1 cm^3 -- earphone scale. Litres would read as 0.001.
        box = doc.addObject("Part::Box", "Small")
        box.Length = box.Width = box.Height = 10.0
        doc.recompute()
        assert "cm^3" in measure_volume(box).describe()

    def test_large_volume_reported_in_litres(self, doc):
        box = doc.addObject("Part::Box", "Big")
        box.Length = box.Width = box.Height = 100.0
        doc.recompute()
        assert "litre" in measure_volume(box).describe()

    def test_object_without_a_solid_is_rejected(self, doc):
        # A sketch encloses no volume; returning 0 would look like an empty cavity.
        sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
        doc.recompute()
        with pytest.raises(NoSolidError):
            measure_volume(sketch)

    def test_partial_failure_still_measures_the_rest(self, doc):
        box = doc.addObject("Part::Box", "Box")
        sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
        doc.recompute()

        measured, problems = measure_volumes([box, sketch])
        assert len(measured) == 1
        assert len(problems) == 1
        assert "Sketch" in problems[0]


class TestSolverDiscovery:
    def test_reports_known_solvers(self):
        from freecad.audio_analysis.solvers import discovery

        discovery.refresh()
        keys = {spec.key for spec, _ in discovery.status()}
        assert {"Ngspice", "Gmsh", "ElmerSolver", "NumCalc"} <= keys

    def test_missing_message_names_the_tier_and_a_fix(self):
        from freecad.audio_analysis.solvers import discovery

        message = discovery.missing_message("ElmerSolver")
        assert "Tier 2" in message
        assert "docs/SETUP.md" in message

    def test_unknown_solver_raises(self):
        from freecad.audio_analysis.solvers import discovery

        with pytest.raises(KeyError):
            discovery.find("NoSuchSolver")

    def test_require_raises_a_helpful_error_when_absent(self):
        from freecad.audio_analysis.solvers import discovery

        if discovery.is_available("NumCalc"):
            pytest.skip("NumCalc is installed on this machine")
        with pytest.raises(RuntimeError, match="NumCalc"):
            discovery.require("NumCalc")
