"""How the network appears in FreeCAD's tree.

A lumped network is a graph, and a tree cannot show a graph. Flattening it -- every
object as a sibling under the analysis -- loses the topology completely, which is the
complaint this answers. Filing each element under the *first* node it connects turns the
tree into an adjacency list, and writing the far end into the description means the
choice of parent hides nothing.

Tested at the objects layer rather than through the view providers, because the parenting
rule is a fact about the model and must stay testable without a GUI (CLAUDE.md).
"""

from __future__ import annotations

import pytest

FreeCAD = pytest.importorskip("FreeCAD")

from freecad.audio_analysis.objects import (  # noqa: E402
    make_analysis,
    make_environment,
    network_objects as no,
    study,
)
from freecad.audio_analysis.objects.crossover import make_crossover  # noqa: E402
from freecad.audio_analysis.objects.network_objects import (  # noqa: E402
    owner_of,
    tree_children,
    unclaimed,
)
from freecad.audio_analysis.templates import apply_template  # noqa: E402


@pytest.fixture
def doc():
    document = FreeCAD.newDocument("tree_test")
    name = document.Name
    yield document
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


@pytest.fixture
def headphone(doc):
    analysis = make_analysis(doc)
    make_environment(doc, analysis)
    apply_template("over_ear_open", doc, analysis)
    doc.recompute()
    return analysis


def named(analysis, label):
    return next(o for o in analysis.Group if o.Label == label)


def layout(analysis) -> dict[str, list[str]]:
    """``{parent label: [child labels]}`` for everything the analysis holds."""
    members = list(analysis.Group)
    tree = {"": [o.Label for o in unclaimed(members)]}
    for member in members:
        children = tree_children(member, members)
        if children:
            tree[member.Label] = [child.Label for child in children]
    return tree


class TestParenting:
    def test_elements_are_filed_under_the_node_they_connect(self, headphone):
        assert owner_of(named(headphone, "Driver")) is named(headphone, "EarCavity")
        assert owner_of(named(headphone, "RearVent")) is named(headphone, "CupCavity")
        assert owner_of(named(headphone, "VentMesh")) is named(headphone, "BehindMesh")

    def test_a_node_owns_nothing_itself(self, headphone):
        assert owner_of(named(headphone, "EarCavity")) is None

    def test_the_tree_reads_as_an_adjacency_list(self, headphone):
        tree = layout(headphone)
        assert tree["EarCavity"] == ["Driver", "PadSeal"]
        assert tree["CupCavity"] == ["RearVent"]
        assert tree["BehindMesh"] == ["VentMesh"]

    def test_nodes_and_study_objects_stay_at_the_top(self, headphone):
        top = layout(headphone)[""]
        for label in ("EarCavity", "CupCavity", "BehindMesh", "FrequencySweep", "LumpedSolver"):
            assert label in top
        for label in ("Driver", "PadSeal", "RearVent", "VentMesh"):
            assert label not in top

    def test_every_object_appears_exactly_once(self, headphone):
        """Two claimers would show an object twice, which is worse than a flat list."""
        tree = layout(headphone)
        appearances = [label for labels in tree.values() for label in labels]
        assert sorted(appearances) == sorted(o.Label for o in headphone.Group)

    def test_an_element_on_the_exterior_at_both_ends_stays_conspicuous(self, doc):
        """Nothing claims it, so it sits at the top -- which is right, since it is almost
        always a wiring mistake."""
        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        no.make_volume(doc, analysis, "Cavity")
        stray = no.make_driver(doc, analysis, "Unconnected")
        doc.recompute()
        assert owner_of(stray) is None
        assert "Unconnected" in layout(analysis)[""]

    def test_the_second_terminal_decides_when_the_first_is_the_exterior(self, doc):
        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        cavity = no.make_volume(doc, analysis, "Cavity")
        port = no.make_port(doc, analysis, "Port")
        port.NodeB = cavity  # NodeA left on the exterior
        doc.recompute()
        assert owner_of(port) is cavity

    def test_a_volume_claims_the_solid_it_measures(self, doc):
        """The extracted air sits with the acoustic object that uses it."""
        import Part

        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        solid = doc.addObject("Part::Feature", "Cavity")
        solid.Shape = Part.makeBox(50, 50, 50)
        analysis.addObject(solid)
        volume = no.make_volume(doc, analysis, "EarCavity")
        volume.Shape = solid
        doc.recompute()

        assert tree_children(volume, list(analysis.Group)) == [solid]
        assert "Cavity" not in layout(analysis)[""]

    def test_reparenting_follows_a_rewired_connection(self, headphone, doc):
        driver = named(headphone, "Driver")
        driver.FrontNode = named(headphone, "CupCavity")
        doc.recompute()
        assert owner_of(driver) is named(headphone, "CupCavity")
        assert layout(headphone)["CupCavity"] == ["Driver", "RearVent"]


class TestDescriptions:
    def test_an_element_names_both_of_its_terminals(self, headphone):
        assert named(headphone, "Driver").Label2 == "EarCavity -> CupCavity"

    def test_the_exterior_is_named_rather_than_left_blank(self, headphone):
        assert named(headphone, "PadSeal").Label2 == "EarCavity -> exterior"

    def test_a_one_terminal_element_says_only_its_own(self, doc):
        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        cavity = no.make_volume(doc, analysis, "Cavity")
        radiation = no.make_radiation(doc, analysis, "Radiation")
        radiation.NodeA = cavity
        doc.recompute()
        assert radiation.Label2 == "Cavity"

    def test_a_crossover_says_which_drivers_it_feeds(self, doc):
        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        cavity = no.make_volume(doc, analysis, "Cavity")
        driver = no.make_driver(doc, analysis, "Woofer")
        driver.FrontNode = cavity
        branch = make_crossover(doc, analysis, "LowPass")
        branch.Drivers = [driver]
        doc.recompute()
        assert branch.Label2 == "feeds Woofer"

    def test_an_unattached_crossover_says_so(self, doc):
        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        branch = make_crossover(doc, analysis, "Orphan")
        doc.recompute()
        assert branch.Label2 == "feeds nothing"

    def test_a_node_is_left_alone(self, headphone):
        """Nodes have no connections of their own, so their description is the user's."""
        cavity = named(headphone, "EarCavity")
        cavity.Label2 = "left channel"
        cavity.Proxy.execute(cavity)
        assert cavity.Label2 == "left channel"

    def test_descriptions_follow_a_rename(self, headphone, doc):
        named(headphone, "CupCavity").Label = "Left cup"
        doc.recompute()
        assert named(headphone, "Driver").Label2 == "EarCavity -> Left cup"


class TestRecomputeStillWorks:
    """The base class took over ``execute``; subclasses now override ``update``.

    Worth pinning, because a silently skipped ``update`` would stop derived values
    refreshing and nothing would fail loudly.
    """

    def test_a_driver_still_derives_qts(self, headphone, doc):
        driver = named(headphone, "Driver")
        driver.Qms, driver.Qes = 4.0, 0.5
        doc.recompute()
        assert driver.Qts == pytest.approx(4.0 * 0.5 / 4.5)

    def test_a_volume_still_measures_its_solid(self, doc):
        import Part

        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        solid = doc.addObject("Part::Feature", "Cavity")
        solid.Shape = Part.makeBox(100, 100, 100)  # 1 litre
        volume = no.make_volume(doc, analysis, "Volume")
        volume.Shape = solid
        doc.recompute()
        assert volume.Volume.getValueAs("l").Value == pytest.approx(1.0)
        assert volume.LargestDimension.getValueAs("mm").Value == pytest.approx(100.0)

    def test_a_crossover_still_derives_its_components(self, doc):
        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        branch = make_crossover(doc, analysis, "LowPass")
        branch.Response, branch.Realisation = "Lowpass", "Passive"
        doc.recompute()
        assert "mH" in branch.Components

    def test_a_crossover_description_follows_a_driver_rename(self, doc):
        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        cavity = no.make_volume(doc, analysis, "Cavity")
        driver = no.make_driver(doc, analysis, "Woofer")
        driver.FrontNode = cavity
        branch = make_crossover(doc, analysis, "LowPass")
        branch.Drivers = [driver]
        doc.recompute()

        driver.Label = "Bass driver"
        assert branch.Label2 == "feeds Bass driver"

    def test_a_rename_needs_no_recompute(self, headphone):
        """The tree updates immediately, because a stale description is worse than none."""
        named(headphone, "CupCavity").Label = "Left cup"
        assert named(headphone, "Driver").Label2 == "EarCavity -> Left cup"

    def test_descriptions_survive_a_reload(self, headphone, doc, tmp_path):
        path = str(tmp_path / "tree.FCStd")
        doc.saveAs(path)
        name = doc.Name
        FreeCAD.closeDocument(name)

        reopened = FreeCAD.openDocument(path)
        try:
            analysis = next(
                o for o in reopened.Objects if getattr(o, "Label", "") == "AudioAnalysis"
            )
            assert named(analysis, "Driver").Label2 == "EarCavity -> CupCavity"
            assert layout(analysis)["EarCavity"] == ["Driver", "PadSeal"]
        finally:
            FreeCAD.closeDocument(reopened.Name)
