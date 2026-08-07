"""Finding a cavity from one pick.

The cases here are the ones that actually went wrong while this was being written, plus
the ones that would go wrong silently. Silence is the theme: every failure mode in seeded
extraction produces a plausible solid and a plausible number, so a test that only checks
"a cavity came back" would have passed throughout.
"""

from __future__ import annotations

import pytest

FreeCAD = pytest.importorskip("FreeCAD")
Part = pytest.importorskip("Part")

from freecad.audio_analysis import seeding  # noqa: E402
from freecad.audio_analysis.cavity import (  # noqa: E402
    BoundarySolid,
    enclosed_regions,
    extract_regions_from_solids,
)
from freecad.audio_analysis.seeding import (  # noqa: E402
    SeedError,
    collect_candidates,
    describe_wetted,
    probe_from_subshape,
    region_for_probe,
    solids_for,
    source_objects,
    wetted_parts,
)

OUTER = 50.0
WALL = 5.0


@pytest.fixture
def doc():
    document = FreeCAD.newDocument("seeding_test")
    name = document.Name
    yield document
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


def sealed_box():
    """A closed hollow cube: one enclosed void, walls belonging to a single solid."""
    outer = Part.makeBox(OUTER, OUTER, OUTER)
    inner = Part.makeBox(
        OUTER - 2 * WALL,
        OUTER - 2 * WALL,
        OUTER - 2 * WALL,
        FreeCAD.Vector(WALL, WALL, WALL),
    )
    return outer.cut(inner)


def open_cup():
    """The same cube with its top removed -- open, so its interior reaches the outside."""
    outer = Part.makeBox(OUTER, OUTER, OUTER - WALL)
    inner = Part.makeBox(
        OUTER - 2 * WALL, OUTER - 2 * WALL, OUTER, FreeCAD.Vector(WALL, WALL, WALL)
    )
    return outer.cut(inner)


def flat_lid():
    """A plate that closes :func:`open_cup`, sitting exactly on its rim.

    The lid's underside is its own bounding box's ``ZMin``, which is the geometry that
    broke wall attribution: see the regression test below.
    """
    return Part.makeBox(OUTER, OUTER, WALL, FreeCAD.Vector(0, 0, OUTER - WALL))


def regions_of(*solids):
    sources = [BoundarySolid(f"part{i}", s) for i, s in enumerate(solids)]
    return sources, extract_regions_from_solids(sources)


def inner_face_of(box_shape, target_z):
    """The horizontal face of a hollow box at height ``target_z``."""
    for face in box_shape.Faces:
        bb = face.BoundBox
        if abs(bb.ZMin - target_z) < 1e-6 and abs(bb.ZLength) < 1e-6:
            return face
    raise AssertionError(f"no horizontal face at z={target_z}")


class TestProbeFromPick:
    def test_a_face_probe_lands_off_the_surface_on_the_air_side(self):
        """A point *on* a face is in neither region; it has to be pushed off it."""
        box = sealed_box()
        floor = inner_face_of(box, WALL)  # the cavity floor, air above it
        probe = probe_from_subshape(floor)

        assert probe.is_directed
        assert probe.kind == "Face"
        # The probe must have moved off the surface, and upward into the cavity.
        assert probe.point.z > probe.surface_point.z
        assert abs(probe.point.z - WALL) == pytest.approx(seeding.PROBE_OFFSET_MM)

    def test_an_edge_probe_is_undirected(self):
        """An edge is shared by two faces whose outward normals disagree."""
        probe = probe_from_subshape(sealed_box().Edges[0])
        assert not probe.is_directed
        assert probe.kind == "Edge"

    def test_a_vertex_probe_is_undirected(self):
        probe = probe_from_subshape(sealed_box().Vertexes[0])
        assert not probe.is_directed
        assert probe.kind == "Vertex"

    def test_an_unusable_pick_says_what_to_pick_instead(self):
        with pytest.raises(SeedError) as raised:
            probe_from_subshape(sealed_box())
        assert "pick a face" in str(raised.value).lower()

    @pytest.mark.parametrize(
        "face",
        [
            pytest.param(
                max(Part.makeCylinder(20.0, 40.0).Faces, key=lambda f: f.Area),
                id="cylinder-wall",
            ),
            pytest.param(
                max(
                    Part.makeCylinder(20.0, 40.0)
                    .cut(Part.makeCylinder(15.0, 40.0))
                    .Faces,
                    key=lambda f: f.Area,
                ),
                id="tube-wall",
            ),
            pytest.param(
                max(Part.makeSphere(15.0).Faces, key=lambda f: f.Area), id="sphere"
            ),
            pytest.param(sealed_box().Faces[0], id="planar"),
        ],
    )
    def test_the_probe_point_always_lies_on_the_face(self, face):
        """The contract everything downstream depends on.

        A curved face's centroid is nowhere near it -- a cylinder's is out on the axis,
        twenty millimetres off the wall. A point that is not on the face is a point in
        mid-air, and every containment test below matches nothing, so the face's whole
        area is written off as belonging to no part.
        """
        point = seeding._point_on_face(face)
        assert face.distToShape(Part.Vertex(point))[0] == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.parametrize(
        "solid",
        [
            pytest.param(sealed_box(), id="hollow-box"),
            pytest.param(open_cup(), id="open-cup"),
            pytest.param(
                Part.makeCylinder(20.0, 40.0).cut(Part.makeCylinder(15.0, 40.0)),
                id="tube",
            ),
            pytest.param(
                Part.makeBox(20.0, 20.0, 20.0, FreeCAD.Vector(-10.0, -10.0, 0.0)).cut(
                    Part.makeCylinder(3.0, 20.0)
                ),
                id="drilled-box",
            ),
        ],
    )
    def test_every_face_probe_lands_in_air_not_in_the_material(self, solid):
        """The invariant the whole directed probe rests on, checked face by face.

        ``Face.normalAt`` already applies the face's orientation, so flipping it again on
        a ``Reversed`` face -- which reads plausibly -- aims the probe *into* the solid.
        Roughly half the faces of any real part are reversed, and nothing complains: the
        probe simply lands in the material, no region contains it, and
        :func:`region_for_probe` quietly falls through to nearest-region matching, which
        prefers the largest touching region. A pick beside a small cavity then returns the
        exterior, and the panel reports a leak that does not exist.

        Testing one face cannot catch this. The old code passed every single-face test in
        this class because those faces happened to be ``Forward``.
        """
        for index, face in enumerate(solid.Faces):
            probe = probe_from_subshape(face)
            assert not solid.isInside(probe.point, 1e-7, False), (
                f"face {index} ({face.Orientation}): the probe landed inside the material"
            )


class TestRegionMatching:
    def test_a_pick_inside_finds_the_enclosed_cavity(self):
        box = sealed_box()
        sources, regions = regions_of(box)
        probe = probe_from_subshape(inner_face_of(box, WALL))

        region = region_for_probe(regions, probe)
        assert region is not None
        assert not region.is_exterior
        inner = OUTER - 2 * WALL
        assert region.volume_mm3 == pytest.approx(inner**3)

    def test_a_pick_outside_finds_the_exterior(self):
        """The same model, the other side of the same wall. Nothing else differs."""
        box = sealed_box()
        sources, regions = regions_of(box)
        probe = probe_from_subshape(inner_face_of(box, 0.0))  # the outer floor

        region = region_for_probe(regions, probe)
        assert region is not None
        assert region.is_exterior

    def test_an_open_model_gives_the_exterior_from_the_inside(self):
        """The signal the panel exists to show: a cavity that reaches the envelope."""
        cup = open_cup()
        sources, regions = regions_of(cup)
        probe = probe_from_subshape(inner_face_of(cup, WALL))

        region = region_for_probe(regions, probe)
        assert region is not None
        assert region.is_exterior
        assert not enclosed_regions(regions)

    def test_capping_the_same_model_encloses_it(self):
        cup, lid = open_cup(), flat_lid()
        sources, regions = regions_of(cup, lid)
        probe = probe_from_subshape(inner_face_of(cup, WALL))

        region = region_for_probe(regions, probe)
        assert region is not None
        assert not region.is_exterior

    def test_an_edge_pick_still_resolves(self):
        """Undirected probes fall back to nearest-region, which must still find it."""
        box = sealed_box()
        sources, regions = regions_of(box)
        floor = inner_face_of(box, WALL)
        probe = probe_from_subshape(floor.Edges[0])

        region = region_for_probe(regions, probe)
        assert region is not None
        assert not region.is_exterior


class TestWettedParts:
    def test_every_wall_is_attributed(self):
        box = sealed_box()
        sources, regions = regions_of(box)
        region = enclosed_regions(regions)[0]

        parts, unattributed = wetted_parts(region, sources)
        assert unattributed == pytest.approx(0.0)
        assert [p.label for p in parts] == ["part0"]
        inner = OUTER - 2 * WALL
        assert parts[0].area_mm2 == pytest.approx(6 * inner**2)

    def test_a_surface_point_a_hair_outside_the_bounding_box_still_counts(self):
        """The regression that hid a cap from the cavity it closed.

        A part is wetted on faces that lie at its own bounding box's limit -- a flat cap's
        entire contact face does. ``BoundBox.isInside`` tolerates a point exactly on the
        boundary but rejects one a nanometre past it, and a surface point computed through
        a boolean lands on either side at random. Ungrown, the box rejects a scattering of
        exactly the points that should match, and the part silently disappears from the
        wall list of the cavity it bounds.
        """
        solid = Part.makeBox(10.0, 10.0, 10.0)
        on_top = FreeCAD.Vector(5.0, 5.0, 10.0 + 1e-9)

        assert not solid.BoundBox.isInside(on_top), "the strictness that caused the bug"
        assert seeding.lies_on(solid, seeding.grown_box(solid), on_top)

    def test_a_part_wetted_only_on_its_contact_face_is_listed(self):
        cup, lid = open_cup(), flat_lid()
        sources, regions = regions_of(cup, lid)
        region = enclosed_regions(regions)[0]

        parts, unattributed = wetted_parts(region, sources)
        labels = {p.label for p in parts}

        assert "part1" in labels, "the lid must appear among the parts bounding its cavity"
        assert unattributed == pytest.approx(0.0)
        inner = OUTER - 2 * WALL
        lid_part = next(p for p in parts if p.label == "part1")
        assert lid_part.area_mm2 == pytest.approx(inner**2)

    def test_shares_are_reported_as_percentages(self):
        cup, lid = open_cup(), flat_lid()
        sources, regions = regions_of(cup, lid)
        region = enclosed_regions(regions)[0]

        parts, unattributed = wetted_parts(region, sources)
        text = describe_wetted(parts, unattributed)

        assert "%" in text
        assert "part1" in text

    def test_no_parts_says_so_rather_than_dividing_by_zero(self):
        assert describe_wetted([], 0.0) == "no bounding parts identified"


class TestContainerExpansion:
    """A link republishes its body's feature tree as its own ``Group``.

    Treating that as a container walks into the construction history and collects every
    intermediate Pad and Pocket as a separate part. On a real assembly that turns twelve
    parts into a hundred-odd overlapping solids, and the fuse never returns -- which is
    exactly how it presented: a hang, with no error to read.
    """

    class FakeLink:
        Name = "Body004"
        Label = "Cup"
        Group = ["Sketch", "Pad", "Pocket", "PolarPattern"]

        @staticmethod
        def isDerivedFrom(kind):
            return kind == "App::Link"

    class FakePart:
        Name = "Assembly"
        Label = "Assembly"
        Group = ["Body", "Body001"]

        @staticmethod
        def isDerivedFrom(kind):
            return kind in ("App::Part", "App::GeoFeature")

    def test_a_link_is_a_leaf_however_many_children_it_advertises(self):
        assert not seeding._is_container(self.FakeLink())

    def test_a_real_container_is_still_expanded(self):
        assert seeding._is_container(self.FakePart())

    def test_an_object_with_no_children_is_a_leaf(self):
        class Bare:
            Group = []

            @staticmethod
            def isDerivedFrom(kind):
                return kind == "App::Part"

        assert not seeding._is_container(Bare())


class TestScopeAndVisibility:
    def _solid(self, doc, name, shape, visible=True):
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = shape
        obj.Visibility = visible
        return obj

    def test_hidden_bodies_are_included_by_default(self, doc):
        self._solid(doc, "Shown", sealed_box())
        self._solid(doc, "Hidden", flat_lid(), visible=False)
        doc.recompute()

        sources, hidden = solids_for(doc.RootObjects)
        assert hidden == []
        assert len(source_objects(sources)) == 2

    def test_hidden_bodies_can_be_excluded_and_are_named_when_they_are(self, doc):
        self._solid(doc, "Shown", sealed_box())
        self._solid(doc, "Hidden", flat_lid(), visible=False)
        doc.recompute()

        sources, hidden = solids_for(doc.RootObjects, include_hidden=False)
        assert hidden == ["Hidden"]
        assert source_objects(sources) == ["Shown"]

    def test_a_hidden_cap_is_kept_even_when_hidden_bodies_are_excluded(self, doc):
        """A cap is routinely hidden once it works; dropping it reopens the cavity."""
        from freecad.audio_analysis.objects.cap_object import make_cap

        self._solid(doc, "Shown", open_cup())
        cap = make_cap(doc)
        cap.Shape = flat_lid()
        cap.Visibility = False

        sources, hidden = solids_for(doc.RootObjects, include_hidden=False)
        assert hidden == [], "a cap must not be reported as skipped"
        assert cap.Label in source_objects(sources)

    def test_a_cavity_is_never_taken_as_a_boundary_part(self, doc):
        """Feeding the air back in would subtract the cavity from itself."""
        from freecad.audio_analysis.objects.cavity_object import make_cavity

        self._solid(doc, "Shell", sealed_box())
        cavity = make_cavity(doc)
        cavity.Shape = Part.makeBox(10, 10, 10, FreeCAD.Vector(WALL, WALL, WALL))

        sources, _ = solids_for(doc.RootObjects)
        assert cavity.Label not in source_objects(sources)

    def test_candidates_are_scoped_to_the_container_the_pick_came_from(self, doc):
        """A second, unrelated assembly must not be dragged into the extraction."""
        part = doc.addObject("App::Part", "Assembly")
        inside = self._solid(doc, "Inside", sealed_box())
        part.addObject(inside)
        self._solid(doc, "Elsewhere", flat_lid())
        doc.recompute()

        sources, _ = collect_candidates(doc, part)
        labels = source_objects(sources)
        assert "Inside" in labels
        assert "Elsewhere" not in labels

    def test_caps_are_collected_from_outside_the_scoped_container(self, doc):
        """Caps belong to the analysis, not to the CAD container the parts live in."""
        from freecad.audio_analysis.objects.cap_object import make_cap

        part = doc.addObject("App::Part", "Assembly")
        inside = self._solid(doc, "Inside", open_cup())
        part.addObject(inside)
        cap = make_cap(doc)
        cap.Shape = flat_lid()

        sources, _ = collect_candidates(doc, part)
        assert cap.Label in source_objects(sources)
