"""Locating a leak once the extraction says a cavity will not close.

The cases here are the ones the real assembly produced. The headline one is worth stating
plainly: the defect that actually broke `assembly_driver_cup` was a cap displaced by one
millimetre, and *growing* it did not help. A test that only checks "an undersized cap is
reported" would have passed while the tool stayed useless on the case it was built for.
"""

from __future__ import annotations

import pytest

FreeCAD = pytest.importorskip("FreeCAD")
Part = pytest.importorskip("Part")

from freecad.audio_analysis import leaks  # noqa: E402
from freecad.audio_analysis.cavity import (  # noqa: E402
    BoundarySolid,
    extract_regions_from_solids,
    make_envelope,
)
from freecad.audio_analysis.checks import Severity  # noqa: E402
from freecad.audio_analysis.leaks import (  # noqa: E402
    Bottleneck,
    find_escape_path,
    near_miss_diagnostics,
    near_misses,
)

OUTER = 30.0
WALL = 4.0
BORE = 2.0


def sources_of(*labelled):
    return [BoundarySolid(label, solid) for label, solid in labelled]


def hollow_box():
    """A closed hollow cube."""
    outer = Part.makeBox(OUTER, OUTER, OUTER)
    inner = Part.makeBox(
        OUTER - 2 * WALL, OUTER - 2 * WALL, OUTER - 2 * WALL,
        FreeCAD.Vector(WALL, WALL, WALL),
    )
    return outer.cut(inner)


def drilled_box():
    """The same cube with a bore through the +z wall — an uncapped opening."""
    bore = Part.makeCylinder(
        BORE, 3 * WALL, FreeCAD.Vector(OUTER / 2, OUTER / 2, OUTER - 2 * WALL),
        FreeCAD.Vector(0, 0, 1),
    )
    return hollow_box().cut(bore)


class TestNearMissScan:
    def test_a_small_gap_is_reported_with_its_size(self):
        a = Part.makeBox(10, 10, 10)
        b = Part.makeBox(10, 10, 10, FreeCAD.Vector(10.1, 0, 0))
        found = near_misses(sources_of(("A", a), ("B", b)))

        assert len(found) == 1
        assert found[0].gap_mm == pytest.approx(0.1, abs=1e-6)
        assert not found[0].is_coincident

    def test_overlapping_parts_are_not_reported(self):
        """Overlap is what sealing means, so an overlapping pair is not a candidate."""
        a = Part.makeBox(10, 10, 10)
        b = Part.makeBox(10, 10, 10, FreeCAD.Vector(9.0, 0, 0))
        assert near_misses(sources_of(("A", a), ("B", b))) == []

    def test_touching_parts_are_flagged_separately_from_a_gap(self):
        """Coincident mating faces are normal; a gap is not. They must not read alike."""
        a = Part.makeBox(10, 10, 10)
        b = Part.makeBox(10, 10, 10, FreeCAD.Vector(10.0, 0, 0))
        found = near_misses(sources_of(("A", a), ("B", b)))

        assert len(found) == 1 and found[0].is_coincident
        codes = [d.code for d in near_miss_diagnostics(sources_of(("A", a), ("B", b)))]
        assert "parts-coincident" in codes
        assert "parts-nearly-touch" not in codes

    def test_parts_beyond_the_limit_are_ignored(self):
        a = Part.makeBox(10, 10, 10)
        b = Part.makeBox(10, 10, 10, FreeCAD.Vector(12.0, 0, 0))
        assert near_misses(sources_of(("A", a), ("B", b))) == []

    def test_a_displaced_cap_is_reported_as_sealing_nothing(self):
        """The real defect: a cap that reaches nothing, because it is in the wrong place.

        Modelled on `assembly_driver_cup`, where seven caps overlapped the cup by
        24.89 mm3 each and the eighth -- missing its 1 mm placement -- overlapped nothing
        and sat 0.019 mm short.
        """
        wall = Part.makeBox(40, 10, 40)
        seated = Part.makeBox(6, 4, 6, FreeCAD.Vector(4, 8, 4))       # overlaps the wall
        adrift = Part.makeBox(6, 4, 6, FreeCAD.Vector(24, 10.02, 4))  # 0.02 mm clear

        found = near_miss_diagnostics(
            sources_of(("Wall", wall), ("CapGood", seated), ("CapBad", adrift)),
            caps=("CapGood", "CapBad"),
        )
        sealing = [d for d in found if d.code == "cap-seals-nothing"]

        assert [d.subject for d in sealing] == ["CapBad"]
        assert sealing[0].severity is Severity.ERROR
        # The remedy has to say the cap is misplaced rather than too small: growing a
        # displaced cap about its own centroid leaves it displaced.
        assert "placement" in sealing[0].remedy.lower()

    def test_a_gap_involving_a_cap_outranks_one_that_does_not(self):
        """The same 0.1 mm gap is a curiosity between two bodies and a fault beside a cap."""
        a = Part.makeBox(10, 10, 10)
        b = Part.makeBox(10, 10, 10, FreeCAD.Vector(10.1, 0, 0))

        def gap_finding(**kwargs):
            found = near_miss_diagnostics(sources_of(("A", a), ("B", b)), **kwargs)
            return next(d for d in found if d.code == "parts-nearly-touch")

        assert gap_finding().severity is Severity.INFO
        assert gap_finding(caps=("B",)).severity is Severity.WARNING
        assert "cap" in gap_finding(caps=("B",)).why.lower()

    def test_a_clean_model_says_so_rather_than_going_quiet(self):
        a = Part.makeBox(10, 10, 10)
        b = Part.makeBox(10, 10, 10, FreeCAD.Vector(30, 0, 0))
        summary = leaks.describe_near_misses(near_miss_diagnostics(sources_of(("A", a), ("B", b))))
        assert "no part comes close" in summary.lower()


class TestNeckFinder:
    @staticmethod
    def _region_and_envelope(solid, seed):
        envelope = make_envelope([solid], padding=2.0)
        regions = extract_regions_from_solids(sources_of(("part", solid)))
        for region in regions:
            if region.shape.isInside(seed, 1e-7, True):
                return region, envelope.BoundBox
        raise AssertionError("the seed is in no region")

    def test_an_uncapped_bore_is_found_and_measured(self):
        solid = drilled_box()
        seed = FreeCAD.Vector(OUTER / 2, OUTER / 2, OUTER / 2)
        region, envelope = self._region_and_envelope(solid, seed)
        assert region.is_exterior, "the drilled box should not enclose its interior"

        found = find_escape_path(region.shape, seed, envelope_box=envelope, resolution=0.5)

        assert found is not None
        # The bore is the only way out, so it must be the bottleneck.
        assert found.width_mm == pytest.approx(2 * BORE, abs=1.0)
        x, y, _z = found.point
        assert x == pytest.approx(OUTER / 2, abs=2.0)
        assert y == pytest.approx(OUTER / 2, abs=2.0)

    def test_a_sealed_cavity_reports_no_way_out(self):
        """Gridding over the envelope, not the region: otherwise every enclosed cavity
        touches its own bounding box and reads as having escaped immediately."""
        solid = hollow_box()
        seed = FreeCAD.Vector(OUTER / 2, OUTER / 2, OUTER / 2)
        region, envelope = self._region_and_envelope(solid, seed)
        assert not region.is_exterior

        assert find_escape_path(region.shape, seed, envelope_box=envelope,
                                resolution=0.5) is None

    def test_the_voxel_volume_is_reported_so_it_can_be_checked(self):
        """The rasterisation is an approximation and must not pretend otherwise."""
        solid = drilled_box()
        seed = FreeCAD.Vector(OUTER / 2, OUTER / 2, OUTER / 2)
        region, envelope = self._region_and_envelope(solid, seed)

        found = find_escape_path(region.shape, seed, envelope_box=envelope, resolution=0.5)

        assert found is not None
        assert found.region_volume_mm3 == pytest.approx(region.volume_mm3)
        drift = abs(found.voxel_volume_mm3 - found.region_volume_mm3)
        assert drift / found.region_volume_mm3 < 0.05

    def test_a_seed_against_a_wall_is_rescued_rather_than_refused(self):
        """A probe sits 0.01 mm off a surface, which can land in the wrong voxel."""
        solid = drilled_box()
        seed = FreeCAD.Vector(OUTER / 2, OUTER / 2, WALL + 0.01)
        region, envelope = self._region_and_envelope(solid, seed)

        found = find_escape_path(region.shape, seed, envelope_box=envelope, resolution=0.5)
        assert found is not None

    def test_the_description_says_what_the_resolution_cannot_see(self):
        text = leaks.describe_escape_path(None, 0.75)
        assert "not proof" in text.lower()
        assert "0.75" in text
