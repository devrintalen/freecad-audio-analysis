"""Cavity extraction: deriving the air from CAD.

The cases that matter are the awkward ones -- an open model with no enclosed void, a
model with several voids, and an assembly full of datum planes that break booleans.
"""

from __future__ import annotations

import pytest

FreeCAD = pytest.importorskip("FreeCAD")
Part = pytest.importorskip("Part")

from freecad.audio_analysis.cavity import (  # noqa: E402
    CavityError,
    collect_solids,
    describe_regions,
    enclosed_regions,
    extract_regions,
    make_envelope,
)
from freecad.audio_analysis.objects import make_analysis  # noqa: E402
from freecad.audio_analysis.objects.cavity_object import ALL_ENCLOSED, make_cavity  # noqa: E402


@pytest.fixture
def doc():
    document = FreeCAD.newDocument("cavity_test")
    name = document.Name
    yield document
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


def hollow_box(doc, name="Shell", outer=50.0, wall=5.0):
    """A closed hollow cube: outer solid minus an inner void."""
    outer_box = Part.makeBox(outer, outer, outer)
    inner = outer - 2 * wall
    inner_box = Part.makeBox(inner, inner, inner, FreeCAD.Vector(wall, wall, wall))
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = outer_box.cut(inner_box)
    doc.recompute()
    return obj


def open_box(doc, name="OpenShell", outer=50.0, wall=5.0):
    """The same shell with a hole through **one** wall, like a cup's ear side.

    The hole must pierce a single wall. Cutting a slab clean through the model would open
    two opposite faces, and then one cap could never close it -- which is how the first
    version of this fixture misled the cap test.
    """
    shell = hollow_box(doc, name, outer, wall)
    hole = Part.makeBox(outer - 4 * wall, wall * 2, outer - 4 * wall,
                        FreeCAD.Vector(2 * wall, -wall / 2, 2 * wall))
    shell.Shape = shell.Shape.cut(hole)
    doc.recompute()
    return shell


def cap_for(doc, outer=50.0, wall=5.0, name="Cap"):
    """A plug that exactly fills the hole cut by :func:`open_box`."""
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeBox(outer - 4 * wall, wall, outer - 4 * wall,
                             FreeCAD.Vector(2 * wall, 0.0, 2 * wall))
    doc.recompute()
    return obj


class TestExtraction:
    def test_closed_shell_yields_its_interior(self, doc):
        shell = hollow_box(doc)
        regions = extract_regions([shell])
        enclosed = enclosed_regions(regions)
        assert len(enclosed) == 1
        # Interior of a 50 mm cube with 5 mm walls is 40^3 = 64000 mm^3.
        assert enclosed[0].volume_mm3 == pytest.approx(64000.0, rel=1e-6)

    def test_exterior_region_is_identified_not_returned_as_a_cavity(self, doc):
        shell = hollow_box(doc)
        regions = extract_regions([shell])
        assert any(r.is_exterior for r in regions)
        assert all(not r.is_exterior for r in enclosed_regions(regions))

    def test_open_shell_has_no_enclosed_cavity(self, doc):
        """The headphone-cup case: interior and outside are one region."""
        shell = open_box(doc)
        regions = extract_regions([shell])
        assert enclosed_regions(regions) == []
        assert "no enclosed cavity" in describe_regions(regions).lower()

    def test_a_cap_closes_an_open_shell(self, doc):
        """Adding a cap is what makes the volume exist -- and where the ear would sit."""
        shell = open_box(doc)
        assert enclosed_regions(extract_regions([shell])) == []  # open: nothing enclosed

        cap = cap_for(doc)
        enclosed = enclosed_regions(extract_regions([shell], [cap]))
        assert len(enclosed) == 1
        # Capped, the interior is the original 40^3 void again.
        assert enclosed[0].volume_mm3 == pytest.approx(40**3, rel=1e-6)

    def test_multiple_voids_are_reported_largest_first(self, doc):
        big = hollow_box(doc, "Big", outer=50.0, wall=5.0)
        small = hollow_box(doc, "Small", outer=20.0, wall=3.0)
        small.Placement.Base.x = 100.0
        doc.recompute()

        enclosed = enclosed_regions(extract_regions([big, small]))
        assert len(enclosed) == 2
        assert enclosed[0].volume_mm3 > enclosed[1].volume_mm3
        assert enclosed[1].volume_mm3 == pytest.approx(14**3, rel=1e-6)

    def test_minimum_volume_drops_slivers(self, doc):
        big = hollow_box(doc, "Big")
        tiny = hollow_box(doc, "Tiny", outer=6.0, wall=1.0)
        tiny.Placement.Base.x = 100.0
        doc.recompute()

        loose = enclosed_regions(extract_regions([big, tiny], minimum_volume=1.0))
        strict = enclosed_regions(extract_regions([big, tiny], minimum_volume=1000.0))
        assert len(loose) == 2
        assert len(strict) == 1

    def test_datum_planes_are_filtered_before_the_boolean(self, doc):
        """An unbounded plane reports a nonsense volume and makes cut() fail outright."""
        shell = hollow_box(doc)
        plane = doc.addObject("App::Plane", "Datum")
        doc.recompute()
        assert len(collect_solids([shell, plane])) == 1
        assert len(enclosed_regions(extract_regions([shell, plane]))) == 1

    def test_no_solids_raises_a_helpful_error(self, doc):
        sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
        doc.recompute()
        with pytest.raises(CavityError, match="no solids"):
            extract_regions([sketch])

    def test_envelope_needs_positive_padding(self, doc):
        shell = hollow_box(doc)
        with pytest.raises(CavityError, match="padding"):
            make_envelope(collect_solids([shell]), padding=0.0)

    def test_custom_envelope_is_honoured(self, doc):
        shell = hollow_box(doc)
        envelope = Part.makeBox(200.0, 200.0, 200.0, FreeCAD.Vector(-75.0, -75.0, -75.0))
        enclosed = enclosed_regions(extract_regions([shell], envelope=envelope))
        assert len(enclosed) == 1
        assert enclosed[0].volume_mm3 == pytest.approx(64000.0, rel=1e-6)


class TestCavityObject:
    def test_object_produces_a_visible_shape(self, doc):
        analysis = make_analysis(doc)
        shell = hollow_box(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [shell]
        cavity.Proxy.extract(cavity)

        assert not cavity.Shape.isNull()
        assert cavity.Shape.Volume == pytest.approx(64000.0, rel=1e-6)
        assert cavity.Volume.getValueAs("cm^3").Value == pytest.approx(64.0, rel=1e-6)

    def test_regions_property_lists_what_was_found(self, doc):
        analysis = make_analysis(doc)
        shell = hollow_box(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [shell]
        cavity.Proxy.extract(cavity)
        assert "cm3" in cavity.Regions
        assert "enclosed" in cavity.Regions

    def test_region_index_selects_among_several(self, doc):
        analysis = make_analysis(doc)
        big = hollow_box(doc, "Big", outer=50.0, wall=5.0)
        small = hollow_box(doc, "Small", outer=20.0, wall=3.0)
        small.Placement.Base.x = 100.0
        doc.recompute()

        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [big, small]
        cavity.RegionIndex = 1
        cavity.Proxy.extract(cavity)
        assert cavity.Shape.Volume == pytest.approx(14**3, rel=1e-6)

    def test_all_enclosed_keeps_every_region(self, doc):
        analysis = make_analysis(doc)
        big = hollow_box(doc, "Big")
        small = hollow_box(doc, "Small", outer=20.0, wall=3.0)
        small.Placement.Base.x = 100.0
        doc.recompute()

        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [big, small]
        cavity.RegionIndex = ALL_ENCLOSED
        cavity.Proxy.extract(cavity)
        assert cavity.Volume.getValueAs("mm^3").Value == pytest.approx(64000.0 + 14**3, rel=1e-6)

    def test_out_of_range_index_falls_back_to_the_largest(self, doc):
        analysis = make_analysis(doc)
        shell = hollow_box(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [shell]
        cavity.RegionIndex = 7
        cavity.Proxy.extract(cavity)
        assert cavity.Shape.Volume == pytest.approx(64000.0, rel=1e-6)

    def test_open_model_reports_the_problem_and_produces_nothing(self, doc):
        analysis = make_analysis(doc)
        shell = open_box(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [shell]
        cavity.Proxy.extract(cavity)
        assert cavity.Volume.getValueAs("mm^3").Value == pytest.approx(0.0)
        assert "no enclosed cavity" in cavity.Regions.lower()

    def test_auto_update_can_be_disabled(self, doc):
        analysis = make_analysis(doc)
        shell = hollow_box(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [shell]
        cavity.AutoUpdate = False
        cavity.Proxy.execute(cavity)
        assert cavity.Regions == "not run"

    def test_empty_boundary_is_reported_not_raised(self, doc):
        analysis = make_analysis(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Proxy.extract(cavity)
        assert "no boundary" in cavity.Regions


class TestCavityDrivesAcousticVolume:
    def test_acoustic_volume_reads_the_cavity(self, doc):
        """The whole point: the number stops being typed."""
        from freecad.audio_analysis.objects import network_objects as no

        analysis = make_analysis(doc)
        shell = hollow_box(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [shell]
        cavity.Proxy.extract(cavity)

        volume = no.make_volume(doc, analysis, "Air")
        volume.Shape = cavity
        volume.Proxy.execute(volume)
        assert volume.Volume.getValueAs("cm^3").Value == pytest.approx(64.0, rel=1e-6)

    def test_volume_follows_a_geometry_change(self, doc):
        """Parametric: resize the part, and the acoustic volume follows."""
        from freecad.audio_analysis.objects import network_objects as no

        analysis = make_analysis(doc)
        shell = hollow_box(doc, outer=50.0, wall=5.0)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [shell]
        cavity.Proxy.extract(cavity)
        volume = no.make_volume(doc, analysis, "Air")
        volume.Shape = cavity
        volume.Proxy.execute(volume)
        before = volume.Volume.getValueAs("mm^3").Value

        # Shrink the shell's interior by thickening the walls.
        outer_box = Part.makeBox(50.0, 50.0, 50.0)
        inner_box = Part.makeBox(30.0, 30.0, 30.0, FreeCAD.Vector(10.0, 10.0, 10.0))
        shell.Shape = outer_box.cut(inner_box)
        doc.recompute()
        cavity.Proxy.extract(cavity)
        volume.Proxy.execute(volume)

        assert volume.Volume.getValueAs("mm^3").Value == pytest.approx(30**3, rel=1e-6)
        assert volume.Volume.getValueAs("mm^3").Value < before


class TestVerdictLeadsTheListing:
    """A real assembly yields many trivial sealed pockets; the verdict must not be buried."""

    def test_open_model_says_so_first(self, doc):
        shell = open_box(doc)
        first_line = describe_regions(extract_regions([shell])).splitlines()[0]
        assert "OPEN MODEL" in first_line

    def test_only_incidental_pockets_reads_as_likely_open(self, doc):
        """The driver_cup case: an open cup whose only sealed voids are screw holes."""
        shell = open_box(doc, outer=200.0, wall=10.0)
        pocket = hollow_box(doc, "ScrewHole", outer=6.0, wall=1.0)
        pocket.Placement.Base.x = 400.0
        doc.recompute()

        text = describe_regions(extract_regions([shell, pocket]))
        assert "LIKELY OPEN" in text.splitlines()[0]
        assert "screw holes" in text

    def test_a_genuine_cavity_reads_as_success(self, doc):
        shell = hollow_box(doc)
        first_line = describe_regions(extract_regions([shell])).splitlines()[0]
        assert "enclosed region" in first_line
        assert "OPEN" not in first_line

    def test_long_listings_are_truncated(self, doc):
        from freecad.audio_analysis.cavity import MAX_LISTED_REGIONS

        boxes = []
        for i in range(MAX_LISTED_REGIONS + 4):
            pocket = hollow_box(doc, f"P{i}", outer=10.0, wall=2.0)
            pocket.Placement.Base.x = i * 40.0
            boxes.append(pocket)
        doc.recompute()

        text = describe_regions(extract_regions(boxes))
        assert "and 4 more" in text or "more" in text.splitlines()[-1]
