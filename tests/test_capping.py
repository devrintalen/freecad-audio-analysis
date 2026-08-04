"""Capping an opening: recovering the loop, and building a plug that actually closes it.

The cases that matter are the ones a real part produces -- a rim made of several edges, a
pattern of ports, and the awkward fact that a cap which merely *touches* the surrounding
material is not the same as one that overlaps it.
"""

from __future__ import annotations

import ast
import inspect
import math

import pytest

FreeCAD = pytest.importorskip("FreeCAD")
Part = pytest.importorskip("Part")

from freecad.audio_analysis import capping  # noqa: E402
from freecad.audio_analysis.capping import (  # noqa: E402
    CapError,
    build_caps,
    cap_solid,
    closed_loops_containing,
    describe_openings,
    edge_names_in,
    grow_face,
    loop_for_edge,
    openings_from_references,
    resolve_reference,
    wires_from_edges,
)
from freecad.audio_analysis.cavity import enclosed_regions, extract_regions  # noqa: E402
from freecad.audio_analysis.objects import make_analysis  # noqa: E402
from freecad.audio_analysis.objects.cap_object import make_cap  # noqa: E402
from freecad.audio_analysis.objects.cavity_object import make_cavity  # noqa: E402

BOX = 50.0
WALL = 5.0
HOLE_RADIUS = 8.0


@pytest.fixture
def doc():
    document = FreeCAD.newDocument("capping_test")
    name = document.Name
    yield document
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


def hollow_box(outer=BOX, wall=WALL):
    """A closed hollow cube -- the shell whose wall the holes go through."""
    inner = outer - 2 * wall
    return Part.makeBox(outer, outer, outer).cut(
        Part.makeBox(inner, inner, inner, FreeCAD.Vector(wall, wall, wall))
    )


def bored_shape(holes=((BOX / 2, BOX / 2),), radius=HOLE_RADIUS):
    """The shell with round holes bored through its **top** wall (+Z).

    Piercing one wall only: a bore straight through the model would open two faces at
    once, and then a single cap could never close it.
    """
    shape = hollow_box()
    for x, y in holes:
        shape = shape.cut(
            Part.makeCylinder(
                radius, WALL * 3, FreeCAD.Vector(x, y, BOX - WALL * 1.5)
            )
        )
    return shape


def bored(doc, name="Shell", holes=((BOX / 2, BOX / 2),), radius=HOLE_RADIUS):
    """:func:`bored_shape` as a document object."""
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = bored_shape(holes, radius)
    doc.recompute()
    return obj


def slotted(doc, name="Slotted"):
    """The shell with a rectangular slot through the top wall.

    A four-edge rim, so propagation has to gather edges rather than find one circle.
    """
    shape = hollow_box().cut(
        Part.makeBox(20.0, 10.0, WALL * 3, FreeCAD.Vector(15.0, 20.0, BOX - WALL * 1.5))
    )
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    doc.recompute()
    return obj


def slot_rim_edges(obj, length=20.0, z=BOX):
    """Indices of the slot's rim edges of a given length, on the top face.

    With the default length these are the slot's two long sides: opposite each other, so
    they share no vertex and cannot form a wire between them.
    """
    return [
        index
        for index, edge in enumerate(obj.Shape.Edges, start=1)
        if abs(edge.BoundBox.ZMin - z) < 1e-6
        and abs(edge.BoundBox.ZMax - z) < 1e-6
        and abs(edge.Length - length) < 1e-6
    ]


def circular_rim_edges(obj, radius=HOLE_RADIUS, z=BOX):
    """Indices of the circular edges of radius ``radius`` lying at height ``z``."""
    found = []
    for index, edge in enumerate(obj.Shape.Edges, start=1):
        curve = edge.Curve
        if not isinstance(curve, Part.Circle):
            continue
        if abs(curve.Radius - radius) > 1e-6:
            continue
        if abs(edge.BoundBox.ZMin - z) > 1e-6:
            continue
        found.append(index)
    return found


class TestFindingTheLoop:
    def test_one_edge_of_a_round_hole_is_already_the_whole_loop(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        edge = shell.Shape.Edges[index - 1]
        loop = loop_for_edge(shell.Shape, edge)

        assert loop.isClosed()
        assert loop.Length == pytest.approx(2 * math.pi * HOLE_RADIUS, rel=1e-6)

    def test_the_hole_wins_over_other_loops_through_the_same_edge(self, doc):
        """The rim edge belongs to several closed wires; only one is the opening."""
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        edge = shell.Shape.Edges[index - 1]

        loops = closed_loops_containing(shell.Shape, edge)
        assert len(loops) >= 1
        assert loops[0].Length == pytest.approx(2 * math.pi * HOLE_RADIUS, rel=1e-6)

    def test_a_slot_rim_beats_the_side_wall_of_its_own_bore(self, doc):
        """The regression that killed 'shortest closed loop wins'.

        A 20x10 slot through a 5 mm wall has a rim of perimeter 60. The side wall of the
        bore is also a closed loop through the same edge, of perimeter 2*(20+5) = 50 --
        shorter. Picking by length caps the side of the bore and leaves the mouth open.
        """
        shell = slotted(doc)
        edge = next(
            e
            for e in shell.Shape.Edges
            if abs(e.BoundBox.ZMin - BOX) < 1e-6
            and abs(e.BoundBox.ZMax - BOX) < 1e-6
            and abs(e.Length - 20.0) < 1e-6
        )
        loop = loop_for_edge(shell.Shape, edge)

        assert Part.Face(loop).Area == pytest.approx(200.0)
        assert loop.Length == pytest.approx(60.0)

    def test_a_slot_rim_expands_from_one_edge_to_four(self, doc):
        shell = slotted(doc)
        rim = slot_rim_edges(shell)
        assert rim, "expected the slot rim to be found"

        loop = loop_for_edge(shell.Shape, shell.Shape.Edges[rim[0] - 1])

        assert loop.isClosed()
        assert len(loop.Edges) == 4
        assert loop.Length == pytest.approx(2 * (20.0 + 10.0))

    def test_edges_are_named_so_the_user_can_check_the_pick(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        loop = loop_for_edge(shell.Shape, shell.Shape.Edges[index - 1])

        assert edge_names_in(shell.Shape, loop) == (f"Edge{index}",)

    def test_an_edge_on_no_closed_loop_is_refused(self, doc):
        wire = doc.addObject("Part::Feature", "Line")
        wire.Shape = Part.makeLine(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(10, 0, 0))
        doc.recompute()

        with pytest.raises(CapError, match="closed loop"):
            loop_for_edge(wire.Shape, wire.Shape.Edges[0])

    def test_an_explicit_closed_selection_is_used_as_given(self, doc):
        shell = slotted(doc)
        edge = next(
            e
            for e in shell.Shape.Edges
            if abs(e.BoundBox.ZMin - BOX) < 1e-6
            and abs(e.BoundBox.ZMax - BOX) < 1e-6
            and abs(e.Length - 20.0) < 1e-6
        )
        loop = loop_for_edge(shell.Shape, edge)

        assert wires_from_edges(loop.Edges)[0].Length == pytest.approx(loop.Length)

    def test_separate_open_edges_form_no_closed_wire(self, doc):
        """Two edges that neither meet nor close: propagation is the only way through."""
        shell = slotted(doc)
        rim = slot_rim_edges(shell)
        opposite = [shell.Shape.Edges[rim[0] - 1], shell.Shape.Edges[rim[1] - 1]]

        assert wires_from_edges(opposite) == []

    def test_a_lone_circular_edge_is_already_a_closed_wire(self, doc):
        """A circle needs no propagation -- it closes on itself, so it is taken as given."""
        shell = bored(doc, holes=((15.0, 15.0), (35.0, 35.0)))
        rims = circular_rim_edges(shell)
        assert len(rims) == 2

        circles = [shell.Shape.Edges[i - 1] for i in rims]
        assert len(wires_from_edges(circles)) == 2


class TestBuildingTheCap:
    def test_the_cap_spans_the_opening(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        loop = loop_for_edge(shell.Shape, shell.Shape.Edges[index - 1])

        cap = cap_solid(loop, thickness=2.0, overlap=0.0)
        expected = math.pi * HOLE_RADIUS**2 * 2.0
        assert cap.Volume == pytest.approx(expected, rel=1e-3)

    def test_the_cap_straddles_the_opening_plane(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        loop = loop_for_edge(shell.Shape, shell.Shape.Edges[index - 1])

        cap = cap_solid(loop, thickness=4.0, overlap=0.0)
        assert cap.BoundBox.ZMin == pytest.approx(BOX - 2.0)
        assert cap.BoundBox.ZMax == pytest.approx(BOX + 2.0)

    def test_overlap_grows_a_circle_by_exactly_the_distance(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        loop = loop_for_edge(shell.Shape, shell.Shape.Edges[index - 1])

        grown = grow_face(Part.Face(loop), 0.5)
        assert grown.Area == pytest.approx(math.pi * (HOLE_RADIUS + 0.5) ** 2, rel=1e-6)

    def test_growth_stays_in_the_plane_and_keeps_its_centre(self, doc):
        """The scale is about the centroid, so the cap widens rather than drifting.

        Worth asserting rather than assuming: a matrix composed the other way round
        scales the *position* too, and the cap silently lands somewhere else.
        """
        shell = bored(doc, holes=((15.0, 35.0),))
        [index] = circular_rim_edges(shell)
        face = Part.Face(loop_for_edge(shell.Shape, shell.Shape.Edges[index - 1]))

        grown = grow_face(face, 0.5)
        assert grown.CenterOfMass.distanceToPoint(face.CenterOfMass) < 1e-9
        assert grown.BoundBox.ZMin == pytest.approx(BOX)
        assert grown.BoundBox.ZMax == pytest.approx(BOX)

    def test_a_non_circular_outline_still_grows(self, doc):
        shell = slotted(doc)
        rim = slot_rim_edges(shell)
        face = Part.Face(loop_for_edge(shell.Shape, shell.Shape.Edges[rim[0] - 1]))

        assert grow_face(face, 0.5).Area > face.Area

    def test_zero_overlap_leaves_the_outline_alone(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        face = Part.Face(loop_for_edge(shell.Shape, shell.Shape.Edges[index - 1]))

        assert grow_face(face, 0.0).Area == pytest.approx(face.Area)

    def test_growth_never_calls_the_kernel_offset(self, doc):
        """A regression guard, not a style preference.

        ``BRepOffsetAPI_MakeOffset`` fails on ordinary port outlines and corrupts the
        kernel as it does, so a sequence of recovered failures segfaults FreeCAD outright.
        Nothing on the capping path may reach it.
        """
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        loop = loop_for_edge(shell.Shape, shell.Shape.Edges[index - 1])

        cap_solid(loop, thickness=2.0, overlap=0.5)  # must not reach the kernel offset

        # Checked against the parsed module rather than its text: the docstrings discuss
        # makeOffset2D at length, and only a real attribute access is a problem.
        tree = ast.parse(inspect.getsource(capping))
        used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "makeOffset2D" not in used

    def test_thickness_must_be_positive(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        loop = loop_for_edge(shell.Shape, shell.Shape.Edges[index - 1])

        with pytest.raises(CapError, match="positive"):
            cap_solid(loop, thickness=0.0)


class TestReferences:
    def test_a_plain_element_name_resolves(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)

        owner, sub = resolve_reference(shell, f"Edge{index}")
        assert isinstance(sub, Part.Edge)
        assert len(owner.Faces) == len(shell.Shape.Faces)

    def test_a_stale_name_says_so(self, doc):
        shell = bored(doc)
        with pytest.raises(CapError, match="could not be resolved"):
            resolve_reference(shell, "Edge9999")

    def test_an_empty_reference_is_refused(self, doc):
        with pytest.raises(CapError, match="no edges or faces"):
            openings_from_references([])

    def test_a_face_contributes_its_outer_wire(self, doc):
        plate = doc.addObject("Part::Feature", "Plate")
        plate.Shape = Part.makeBox(10.0, 10.0, 1.0)
        doc.recompute()

        top = next(
            index
            for index, face in enumerate(plate.Shape.Faces, start=1)
            if abs(face.CenterOfMass.z - 1.0) < 1e-6
        )
        [opening] = openings_from_references([(plate, (f"Face{top}",))])
        assert opening.area_mm2 == pytest.approx(100.0)

    def test_one_edge_per_hole_caps_a_pattern_in_one_object(self, doc):
        shell = bored(doc, holes=((15.0, 15.0), (35.0, 35.0), (15.0, 35.0)))
        rims = circular_rim_edges(shell)
        assert len(rims) == 3

        shape, openings = build_caps([(shell, tuple(f"Edge{i}" for i in rims))])
        assert len(openings) == 3
        assert len(shape.Solids) == 3

    def test_two_edges_of_the_same_rim_give_one_cap(self, doc):
        shell = slotted(doc)
        rim = slot_rim_edges(shell)
        assert len(rim) == 2

        _, openings = build_caps([(shell, (f"Edge{rim[0]}", f"Edge{rim[1]}"))])
        assert len(openings) == 1
        assert openings[0].area_mm2 == pytest.approx(200.0)

    def test_propagation_can_be_turned_off(self, doc):
        shell = slotted(doc)
        rim = slot_rim_edges(shell)

        with pytest.raises(CapError, match="Propagate is off"):
            openings_from_references(
                [(shell, tuple(f"Edge{i}" for i in rim))], propagate=False
            )


class TestAssembliesAndExternalDocuments:
    """The case a real model is always in: parts are links into other documents.

    Both halves of this bit the driver_cup model. ``PropertyLinkSubList`` refuses an
    external object outright, and resolving a pick down to the part that owns the edge
    throws away the assembly transform.
    """

    @pytest.fixture
    def linked(self, doc, tmp_path):
        """A shell in its own document, linked into ``doc`` and moved 100 mm away.

        Both documents have to exist on disk: FreeCAD refuses to create a cross-document
        link unless the linked *and* the owning document have been saved, since the link
        is stored as a file reference. That is also why every part in a real assembly is
        external, and why the reference has to be an XLink.
        """
        source = FreeCAD.newDocument("capping_source")
        part = source.addObject("Part::Feature", "Shell")
        part.Shape = bored_shape()
        source.recompute()
        source.saveAs(str(tmp_path / "capping_source.FCStd"))
        doc.saveAs(str(tmp_path / "capping_main.FCStd"))

        link = doc.addObject("App::Link", "CupLink")
        link.LinkedObject = part
        link.Placement = FreeCAD.Placement(
            FreeCAD.Vector(100.0, 0.0, 0.0), FreeCAD.Rotation()
        )
        doc.recompute()
        yield link, part
        if "capping_source" in FreeCAD.listDocuments():
            FreeCAD.closeDocument("capping_source")

    def test_the_opening_property_accepts_an_external_object(self, doc, linked):
        _, part = linked
        index = circular_rim_edges(part)[0]

        cap = make_cap(doc)
        cap.Opening = [(part, (f"Edge{index}",))]  # must not raise

        assert cap.Opening[0][0] is part

    def test_a_reference_through_a_link_is_placed_where_the_part_sits(self, doc, linked):
        """The placement trap: a cap built in the part's own frame lands 100 mm away."""
        link, part = linked
        index = circular_rim_edges(part)[0]

        _, edge = resolve_reference(link, f"Edge{index}")
        assert edge.CenterOfMass.x == pytest.approx(
            part.Shape.Edges[index - 1].CenterOfMass.x + 100.0
        )

    def test_a_cap_through_a_link_closes_the_assembled_shell(self, doc, linked):
        link, part = linked
        index = circular_rim_edges(part)[0]

        cap = make_cap(doc)
        cap.Opening = [(link, (f"Edge{index}",))]
        cap.Proxy.build(cap)
        doc.recompute()

        assert not cap.Shape.isNull()
        expected = part.Shape.Edges[index - 1].CenterOfMass.x + 100.0
        assert cap.Shape.BoundBox.Center.x == pytest.approx(expected, abs=1e-6)
        assert enclosed_regions(extract_regions([link], [cap]))


class TestTheCapActuallyCloses:
    """The acceptance test: does extraction find an enclosed void afterwards?

    Everything above measures the cap. This measures the thing the cap exists for.
    """

    def test_an_open_shell_has_no_enclosed_void(self, doc):
        shell = bored(doc)
        assert enclosed_regions(extract_regions([shell])) == []

    def test_the_generated_cap_closes_it(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        cap = make_cap(doc, name="Cap")
        cap.Opening = [(shell, (f"Edge{index}",))]
        cap.Proxy.build(cap)
        doc.recompute()

        regions = enclosed_regions(extract_regions([shell], [cap]))
        assert regions, "the cap should have closed the shell"

        inner = BOX - 2 * WALL
        assert regions[0].volume_mm3 == pytest.approx(inner**3, rel=0.02)

    def test_a_cap_that_only_touches_still_needs_no_special_casing(self, doc):
        """Zero overlap is the fragile case, so it is exercised deliberately."""
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        cap = make_cap(doc, name="Cap")
        cap.Opening = [(shell, (f"Edge{index}",))]
        cap.Overlap = FreeCAD.Units.Quantity(0.0, "mm")
        cap.Proxy.build(cap)
        doc.recompute()

        assert enclosed_regions(extract_regions([shell], [cap]))

    def test_several_ports_are_all_closed_by_one_cap_object(self, doc):
        shell = bored(doc, holes=((15.0, 15.0), (35.0, 35.0)))
        rims = circular_rim_edges(shell)
        cap = make_cap(doc, name="Cap")
        cap.Opening = [(shell, tuple(f"Edge{i}" for i in rims))]
        cap.Proxy.build(cap)
        doc.recompute()

        assert enclosed_regions(extract_regions([shell], [cap]))


class TestCapObject:
    def test_it_reports_the_open_area_not_the_grown_one(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        cap = make_cap(doc)
        cap.Opening = [(shell, (f"Edge{index}",))]
        cap.Overlap = FreeCAD.Units.Quantity(1.0, "mm")
        cap.Proxy.build(cap)

        area = cap.OpeningArea.getValueAs("mm^2").Value
        assert area == pytest.approx(math.pi * HOLE_RADIUS**2, rel=1e-3)

    def test_the_listing_names_the_edges_it_used(self, doc):
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        cap = make_cap(doc)
        cap.Opening = [(shell, (f"Edge{index}",))]
        cap.Proxy.build(cap)

        assert f"Edge{index}" in cap.Openings
        assert "planar" in cap.Openings

    def test_no_reference_is_reported_not_raised(self, doc):
        cap = make_cap(doc)
        cap.Proxy.build(cap)

        assert "no opening referenced" in cap.Openings
        assert cap.Shape.isNull()

    def test_a_bad_reference_is_reported_not_raised(self, doc):
        shell = bored(doc)
        cap = make_cap(doc)
        cap.Opening = [(shell, ("Edge9999",))]
        cap.Proxy.build(cap)

        assert cap.Openings.startswith("FAILED")
        assert cap.Shape.isNull()

    def test_it_follows_a_geometry_change(self, doc):
        """The reason the cap is parametric rather than a one-shot solid."""
        shell = bored(doc)
        [index] = circular_rim_edges(shell)
        cap = make_cap(doc)
        cap.Opening = [(shell, (f"Edge{index}",))]
        cap.Proxy.build(cap)
        before = cap.OpeningArea.getValueAs("mm^2").Value

        shell.Shape = bored(doc, name="Bigger", radius=12.0).Shape
        doc.recompute()
        cap.Proxy.build(cap)

        after = cap.OpeningArea.getValueAs("mm^2").Value
        assert after > before
        assert after == pytest.approx(math.pi * 12.0**2, rel=1e-3)

    def test_it_joins_the_analysis_when_there_is_one(self, doc):
        analysis = make_analysis(doc)
        cap = make_cap(doc, analysis)

        assert cap in analysis.Group

    def test_it_drives_a_cavity_end_to_end(self, doc):
        """Cap, then extract, then read the volume -- the whole point of the command."""
        shell = bored(doc)
        [index] = circular_rim_edges(shell)

        cap = make_cap(doc)
        cap.Opening = [(shell, (f"Edge{index}",))]
        cap.Proxy.build(cap)

        cavity = make_cavity(doc)
        cavity.Boundary = [shell]
        cavity.Caps = [cap]
        cavity.Proxy.extract(cavity)

        inner = BOX - 2 * WALL
        assert cavity.Volume.getValueAs("mm^3").Value == pytest.approx(inner**3, rel=0.02)


class TestDescription:
    def test_it_leads_with_the_total_open_area(self, doc):
        shell = bored(doc, holes=((15.0, 15.0), (35.0, 35.0)))
        _, openings = build_caps([(shell, tuple(f"Edge{i}" for i in circular_rim_edges(shell)))])

        text = describe_openings(openings)
        assert text.startswith("2 opening(s)")
        assert "Port" in text

    def test_nothing_found_says_so(self):
        assert describe_openings([]) == "no openings found"
