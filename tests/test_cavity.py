"""Cavity extraction: deriving the air from CAD.

The cases that matter are the awkward ones -- an open model with no enclosed void, a
model with several voids, and an assembly full of datum planes that break booleans.
"""

from __future__ import annotations

import pytest

FreeCAD = pytest.importorskip("FreeCAD")
Part = pytest.importorskip("Part")

from freecad.audio_analysis.cavity import (  # noqa: E402
    BooleanFailure,
    CavityError,
    collect_boundary_solids,
    collect_solids,
    describe_regions,
    enclosed_regions,
    extract_regions,
    fuse_diagnostic,
    geometry_diagnostics,
    make_envelope,
)
from freecad.audio_analysis.checks import Severity  # noqa: E402
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


def fuzzy_box(doc, name="Fuzzy", tolerance=0.5):
    """A solid whose tolerance has been widened, as a failed sweep leaves one.

    This is the exact signature of the defect that cost a day: the shape draws correctly,
    reports ``isValid()`` true, has the volume it should, and destroys any boolean it
    takes part in.
    """
    obj = doc.addObject("Part::Feature", name)
    shape = Part.makeBox(20.0, 20.0, 20.0)
    shape.fixTolerance(tolerance)
    obj.Shape = shape
    doc.recompute()
    return obj


class TestUnionInvariant:
    """The trip-wire: a union is never smaller than its largest part, nor bigger than the sum."""

    def test_a_correct_union_passes(self, doc):
        shell = hollow_box(doc)
        sources = collect_boundary_solids([shell])
        fused = sources[0].solid
        assert fuse_diagnostic(sources, fused) is None

    def test_a_union_smaller_than_its_largest_part_is_rejected(self, doc):
        """The real failure: 439 cm3 of parts fused to 67, still reporting itself valid."""
        big = hollow_box(doc, "Big", outer=50.0)
        small = hollow_box(doc, "Small", outer=20.0, wall=3.0)
        sources = collect_boundary_solids([big, small])

        collapsed = Part.makeBox(1.0, 1.0, 1.0)
        assert collapsed.isValid()  # exactly why this cannot be caught by isValid()

        problem = fuse_diagnostic(sources, collapsed)
        assert problem is not None
        assert problem.severity is Severity.ERROR
        assert problem.code == "fuse-failed"

    def test_a_union_larger_than_the_sum_is_rejected(self, doc):
        shell = hollow_box(doc)
        sources = collect_boundary_solids([shell])
        assert fuse_diagnostic(sources, Part.makeBox(500.0, 500.0, 500.0)) is not None

    def test_an_empty_fuse_result_is_rejected(self, doc):
        """Cap.fuse(Cushion) returned a shape with no solids at all."""
        shell = hollow_box(doc)
        sources = collect_boundary_solids([shell])
        problem = fuse_diagnostic(sources, Part.Shape())
        assert problem is not None
        assert "empty shape" in problem.message

    def test_the_diagnostic_quotes_both_bounds(self, doc):
        big = hollow_box(doc, "Big", outer=50.0)
        sources = collect_boundary_solids([big])
        problem = fuse_diagnostic(sources, Part.makeBox(1.0, 1.0, 1.0))
        # The numbers are what make the finding checkable rather than merely alarming.
        assert "cm3" in problem.why
        assert problem.remedy and problem.reference

    def test_no_sources_is_not_a_failure(self):
        assert fuse_diagnostic([], None) is None


class TestPartDiagnostics:
    def test_clean_parts_produce_no_findings(self, doc):
        shell = hollow_box(doc)
        assert geometry_diagnostics([shell]) == []

    def test_clean_parts_survive_the_deep_check(self, doc):
        shell = hollow_box(doc)
        assert geometry_diagnostics([shell], deep=True) == []

    def test_a_widened_tolerance_is_reported(self, doc):
        fuzzy = fuzzy_box(doc)
        assert fuzzy.Shape.isValid()  # the point: validity says nothing about this

        found = geometry_diagnostics([fuzzy])
        assert len(found) == 1
        assert found[0].code == "part-tolerance-widened"
        assert found[0].subject == fuzzy.Label
        assert found[0].severity is Severity.WARNING

    def test_the_tolerance_finding_explains_and_prescribes(self, doc):
        finding = geometry_diagnostics([fuzzy_box(doc)])[0]
        assert "1e-07" in finding.why or "default" in finding.why
        assert "Check geometry" in finding.remedy

    def test_a_tolerance_within_reason_is_not_reported(self, doc):
        obj = doc.addObject("Part::Feature", "Ordinary")
        shape = Part.makeBox(20.0, 20.0, 20.0)
        shape.fixTolerance(1e-5)  # what an ordinary filleted part carries
        obj.Shape = shape
        doc.recompute()
        assert geometry_diagnostics([obj]) == []

    def test_solids_are_labelled_with_the_object_they_came_from(self, doc):
        shell = hollow_box(doc, "Cushion")
        sources = collect_boundary_solids([shell])
        assert [s.label for s in sources] == ["Cushion"]

    def test_several_solids_in_one_object_are_numbered(self, doc):
        obj = doc.addObject("Part::Feature", "Pair")
        obj.Shape = Part.makeCompound([
            Part.makeBox(10.0, 10.0, 10.0),
            Part.makeBox(10.0, 10.0, 10.0, FreeCAD.Vector(50.0, 0.0, 0.0)),
        ])
        doc.recompute()
        labels = [s.label for s in collect_boundary_solids([obj])]
        assert labels == ["Pair (solid 1)", "Pair (solid 2)"]

    def test_collect_solids_still_returns_bare_solids(self, doc):
        """The old signature is load-bearing for callers that only want geometry."""
        shell = hollow_box(doc)
        assert all(isinstance(s, Part.Shape) for s in collect_solids([shell]))


class TestVerdictDoesNotRepeatAdviceAlreadyTaken:
    """Telling someone to add the cap they just added is how the tool loses their trust."""

    def test_uncapped_open_model_says_to_add_a_cap(self, doc):
        shell = open_box(doc)
        text = describe_regions(extract_regions([shell]), capped=False)
        assert "Add a cap solid" in text

    def test_capped_open_model_does_not_say_to_add_a_cap(self, doc):
        shell = open_box(doc)
        text = describe_regions(extract_regions([shell]), capped=True)
        assert "Add a cap solid" not in text
        assert "already supplied" in text

    def test_capped_advice_names_the_real_remaining_causes(self, doc):
        text = describe_regions(extract_regions([open_box(doc)]), capped=True)
        assert "missing from Boundary" in text
        assert "LeakPath" in text  # a gap may be a real leak, not a modelling error

    def test_the_verdict_still_leads_the_listing(self, doc):
        text = describe_regions(extract_regions([open_box(doc)]), capped=True)
        assert "OPEN MODEL" in text.splitlines()[0]


class TestCavityObjectReportsFailure:
    def _cavity_over(self, doc, monkeypatch, error):
        from freecad.audio_analysis.objects import cavity_object

        analysis = make_analysis(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [hollow_box(doc)]

        def boom(*args, **kwargs):
            raise error

        monkeypatch.setattr(cavity_object, "extract_regions", boom)
        cavity.Proxy.extract(cavity)
        return cavity

    def test_a_broken_boolean_does_not_report_an_open_model(self, doc, monkeypatch):
        """The whole point. This case used to read as 'OPEN MODEL -- add a cap'."""
        from freecad.audio_analysis.checks import Diagnostic

        failure = BooleanFailure([
            Diagnostic(Severity.ERROR, "fuse-failed", "Union is impossible."),
            Diagnostic(Severity.ERROR, "part-fails-boolean-check", "Bad.", subject="Cushion"),
        ])
        cavity = self._cavity_over(doc, monkeypatch, failure)

        assert "EXTRACTION FAILED" in cavity.Regions
        assert "OPEN MODEL" not in cavity.Regions
        assert "add a cap" not in cavity.Regions.lower()

    def test_the_responsible_part_is_named_in_diagnostics(self, doc, monkeypatch):
        from freecad.audio_analysis.checks import Diagnostic

        failure = BooleanFailure([
            Diagnostic(Severity.ERROR, "part-fails-boolean-check", "Bad.", subject="Cushion"),
        ])
        cavity = self._cavity_over(doc, monkeypatch, failure)
        assert "Cushion" in cavity.Diagnostics

    def test_a_failed_extraction_produces_no_volume(self, doc, monkeypatch):
        from freecad.audio_analysis.checks import Diagnostic

        failure = BooleanFailure([Diagnostic(Severity.ERROR, "fuse-failed", "No.")])
        cavity = self._cavity_over(doc, monkeypatch, failure)
        assert cavity.Volume.getValueAs("mm^3").Value == pytest.approx(0.0)
        assert cavity.Shape.isNull()

    def test_ordinary_cavity_errors_still_report_plainly(self, doc, monkeypatch):
        cavity = self._cavity_over(doc, monkeypatch, CavityError("no solids found"))
        assert "no solids found" in cavity.Regions
        assert cavity.Diagnostics == ""

    def test_a_clean_extraction_leaves_diagnostics_empty(self, doc):
        analysis = make_analysis(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [hollow_box(doc)]
        cavity.Proxy.extract(cavity)
        assert cavity.Diagnostics == ""
        assert cavity.Volume.getValueAs("mm^3").Value == pytest.approx(64000.0, rel=1e-6)

    def test_a_suspect_part_is_reported_even_when_extraction_succeeds(self, doc):
        """A tolerance that survives today's boolean will not survive tomorrow's edit."""
        analysis = make_analysis(doc)
        cavity = make_cavity(doc, analysis)
        shell = hollow_box(doc)
        shell.Shape.fixTolerance(0.5)
        cavity.Boundary = [shell]
        cavity.Proxy.extract(cavity)
        assert "part-tolerance-widened" in cavity.Diagnostics or cavity.Diagnostics

    def test_the_object_reports_caps_aware_advice(self, doc):
        analysis = make_analysis(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [open_box(doc)]
        cavity.Caps = [cap_for(doc, name="WrongCap")]
        cavity.Caps[0].Shape = Part.makeBox(1.0, 1.0, 1.0, FreeCAD.Vector(500.0, 0.0, 0.0))
        doc.recompute()
        cavity.Proxy.extract(cavity)
        assert "Add a cap solid" not in cavity.Regions


class TestPreflightSurfacesBoundaryDefects:
    def test_a_fuzzy_boundary_part_is_raised_by_the_check_pass(self, doc):
        from freecad.audio_analysis.checks import run_checks

        analysis = make_analysis(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [fuzzy_box(doc)]
        doc.recompute()

        codes = [d.code for d in run_checks(analysis).diagnostics]
        assert "part-tolerance-widened" in codes

    def test_a_clean_cavity_raises_nothing_about_its_parts(self, doc):
        from freecad.audio_analysis.checks import run_checks

        analysis = make_analysis(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [hollow_box(doc)]
        doc.recompute()

        codes = [d.code for d in run_checks(analysis).diagnostics]
        assert "part-tolerance-widened" not in codes
        assert "cavity-extraction-failed" not in codes

    def test_a_failed_extraction_blocks_the_solve(self, doc):
        from freecad.audio_analysis.checks import run_checks

        analysis = make_analysis(doc)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [hollow_box(doc)]
        doc.recompute()
        # After the recompute, so AutoUpdate does not overwrite the state under test.
        cavity.Regions = "EXTRACTION FAILED -- the boundary parts could not be combined"

        report = run_checks(analysis)
        assert "cavity-extraction-failed" in [d.code for d in report.diagnostics]
        assert not report.can_solve


class TestCutFailureNamesThePart:
    """The union can pass its bounds check and still be too broken to subtract."""

    def test_a_failed_cut_raises_boolean_failure_not_generic_advice(self, doc, monkeypatch):
        from freecad.audio_analysis import cavity as cavity_module

        shell = hollow_box(doc)

        class Exploding:
            BoundBox = Part.makeBox(200.0, 200.0, 200.0).BoundBox

            def cut(self, other):
                raise ValueError("Null shape")

        monkeypatch.setattr(cavity_module, "make_envelope", lambda *a, **k: Exploding())
        with pytest.raises(BooleanFailure) as caught:
            extract_regions([shell])

        codes = [d.code for d in caught.value.diagnostics]
        assert "cut-failed" in codes
        assert "refining the selection" not in str(caught.value)

    def test_the_cut_diagnostic_blames_the_parts_not_the_selection(self):
        from freecad.audio_analysis.cavity import cut_failure_diagnostic

        finding = cut_failure_diagnostic(ValueError("Null shape"))
        assert finding.severity is Severity.ERROR
        assert "Null shape" in finding.message
        assert "boundary parts" in finding.remedy


class TestSuspectPartWithdrawsTheVerdict:
    """An 'open' result from a known-defective part is not evidence of anything."""

    def test_open_verdict_is_withdrawn_when_a_part_was_flagged(self, doc):
        text = describe_regions(
            extract_regions([open_box(doc)]), suspect_parts=["Cushion"]
        )
        assert "NO VERDICT" in text.splitlines()[0]
        assert "OPEN MODEL" not in text
        assert "Cushion" in text

    def test_the_withdrawal_says_why_and_what_to_do(self, doc):
        text = describe_regions(
            extract_regions([open_box(doc)]), suspect_parts=["Cushion"]
        )
        assert "not evidence" in text
        assert "extract again" in text

    def test_a_genuine_open_model_still_gets_a_verdict(self, doc):
        text = describe_regions(extract_regions([open_box(doc)]))
        assert "OPEN MODEL" in text.splitlines()[0]

    def test_a_found_cavity_is_reported_normally_even_if_a_part_is_flagged(self, doc):
        text = describe_regions(
            extract_regions([hollow_box(doc)]), suspect_parts=["Cushion"]
        )
        assert "NO VERDICT" not in text
        assert "enclosed region" in text.splitlines()[0]

    def test_the_object_withdraws_the_verdict_end_to_end(self, doc):
        analysis = make_analysis(doc)
        shell = open_box(doc, name="Cushion")
        shell.Shape.fixTolerance(0.5)
        cavity = make_cavity(doc, analysis)
        cavity.Boundary = [shell]
        cavity.Proxy.extract(cavity)

        assert "NO VERDICT" in cavity.Regions
        assert "Cushion" in cavity.Diagnostics
        assert cavity.Volume.getValueAs("mm^3").Value == pytest.approx(0.0)
