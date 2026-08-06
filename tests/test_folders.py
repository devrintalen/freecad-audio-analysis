"""Caps and cavities get folders of their own, the way an Assembly files its joints.

The cases that matter are the ones where a folder could quietly do the wrong thing: file
an object twice, lose one that predates the arrangement, or fork into a second folder
because someone renamed the first.
"""

from __future__ import annotations

import pytest

FreeCAD = pytest.importorskip("FreeCAD")
Part = pytest.importorskip("Part")

from freecad.audio_analysis.objects import folders  # noqa: E402
from freecad.audio_analysis.objects.analysis import make_analysis  # noqa: E402
from freecad.audio_analysis.objects.cap_object import make_cap  # noqa: E402
from freecad.audio_analysis.objects.cavity_object import make_cavity  # noqa: E402
from freecad.audio_analysis.objects.folders import CAPS, CAVITIES  # noqa: E402
from freecad.audio_analysis.objects import network_objects as no  # noqa: E402


@pytest.fixture
def doc():
    document = FreeCAD.newDocument("folders_test")
    name = document.Name
    yield document
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


def names(group):
    return sorted(child.Name for child in getattr(group, "Group", []) or [])


class TestFiling:
    def test_a_new_cap_lands_in_a_caps_folder(self, doc):
        analysis = make_analysis(doc)
        cap = make_cap(doc, analysis)

        folder = folders.find_folder(analysis, CAPS)
        assert folder is not None
        assert cap.Name in names(folder)
        assert folder.Name in names(analysis)

    def test_a_new_cavity_lands_in_a_cavities_folder(self, doc):
        analysis = make_analysis(doc)
        cavity = make_cavity(doc, analysis)

        folder = folders.find_folder(analysis, CAVITIES)
        assert folder is not None
        assert cavity.Name in names(folder)

    def test_the_object_is_in_the_folder_and_not_also_loose(self, doc):
        """Two parents means the tree draws it twice, with no clue which copy is real."""
        analysis = make_analysis(doc)
        cap = make_cap(doc, analysis)

        assert cap.Name not in names(analysis)
        parents = [p.Name for p in cap.InList if cap.Name in names(p)]
        assert len(parents) == 1

    def test_caps_and_cavities_get_separate_folders(self, doc):
        analysis = make_analysis(doc)
        cap = make_cap(doc, analysis)
        cavity = make_cavity(doc, analysis)

        assert names(folders.find_folder(analysis, CAPS)) == [cap.Name]
        assert names(folders.find_folder(analysis, CAVITIES)) == [cavity.Name]

    def test_several_caps_share_one_folder(self, doc):
        analysis = make_analysis(doc)
        made = [make_cap(doc, analysis, f"Cap{i}") for i in range(4)]

        folder = folders.find_folder(analysis, CAPS)
        assert names(folder) == sorted(c.Name for c in made)
        assert sum(1 for c in analysis.Group if folders.is_folder(c, CAPS)) == 1

    def test_no_folder_appears_until_there_is_something_for_it(self, doc):
        """An empty Caps folder on an analysis with no caps is clutter, not structure."""
        analysis = make_analysis(doc)
        no.make_node(doc, analysis, "Node")

        assert folders.find_folder(analysis, CAPS) is None
        assert folders.find_folder(analysis, CAVITIES) is None

    def test_an_analysis_free_object_is_left_alone(self, doc):
        """make_cap with no analysis must still work; there is nowhere to file it."""
        cap = make_cap(doc, None)
        assert cap is not None
        assert not cap.InList


class TestSweepingUpWhatCameBefore:
    def test_loose_caps_are_swept_in_when_the_next_one_is_made(self, doc):
        """A document written before the folders existed must not stay half-organised."""
        analysis = make_analysis(doc)
        older = make_cap(doc, None, "OlderCap")
        analysis.addObject(older)          # loose, the way it used to be
        assert older.Name in names(analysis)

        make_cap(doc, analysis, "NewCap")

        folder = folders.find_folder(analysis, CAPS)
        assert older.Name in names(folder)
        assert older.Name not in names(analysis)

    def test_making_a_cap_also_tidies_loose_cavities(self, doc):
        """Otherwise the one kind nobody happens to re-create stays loose forever."""
        analysis = make_analysis(doc)
        stray = make_cavity(doc, None, "OlderCavity")
        analysis.addObject(stray)

        make_cap(doc, analysis)

        assert stray.Name in names(folders.find_folder(analysis, CAVITIES))

    def test_organise_reports_what_it_moved_and_is_idempotent(self, doc):
        analysis = make_analysis(doc)
        loose = make_cap(doc, None, "Loose")
        analysis.addObject(loose)

        assert [o.Name for o in folders.organise(analysis)] == [loose.Name]
        assert folders.organise(analysis) == []


class TestFindingTheFolder:
    def test_a_renamed_folder_is_still_found(self, doc):
        """Matching on the label would fork a second folder the moment anyone renamed it."""
        analysis = make_analysis(doc)
        first = make_cap(doc, analysis, "First")
        folder = folders.find_folder(analysis, CAPS)
        folder.Label = "Plugs and bungs"

        second = make_cap(doc, analysis, "Second")

        assert folders.find_folder(analysis, CAPS).Name == folder.Name
        assert names(folder) == sorted([first.Name, second.Name])
        assert sum(1 for c in analysis.Group if folders.is_folder(c, CAPS)) == 1

    def test_a_folder_is_identified_by_its_marker_not_its_type(self, doc):
        """A plain group the user made themselves must not be mistaken for ours."""
        analysis = make_analysis(doc)
        theirs = doc.addObject("App::DocumentObjectGroup", "MyStuff")
        analysis.addObject(theirs)

        assert not folders.is_folder(theirs)
        assert folders.find_folder(analysis, CAPS) is None

    def test_members_finds_them_whether_foldered_or_loose(self, doc):
        analysis = make_analysis(doc)
        loose = make_cap(doc, None, "Loose")
        analysis.addObject(loose)
        filed = make_cap(doc, analysis, "Filed")   # sweeps `loose` in as a side effect

        found = {o.Name for o in folders.members(analysis, CAPS)}
        assert found == {loose.Name, filed.Name}


class TestTreeStaysHonest:
    def test_the_analysis_shows_the_folder_and_not_its_contents(self, doc):
        from freecad.audio_analysis.objects.network_objects import unclaimed

        analysis = make_analysis(doc)
        cap = make_cap(doc, analysis)
        doc.recompute()

        top = [o.Name for o in unclaimed(list(analysis.Group))]
        assert folders.find_folder(analysis, CAPS).Name in top
        assert cap.Name not in top

    def test_a_volume_still_claims_a_plain_solid_it_measures(self, doc):
        """Folders collect our own cavity objects; an ordinary solid is unaffected."""
        from freecad.audio_analysis.objects.network_objects import tree_children

        analysis = make_analysis(doc)
        solid = doc.addObject("Part::Feature", "Slab")
        solid.Shape = Part.makeBox(10, 10, 10)
        analysis.addObject(solid)
        volume = no.make_volume(doc, analysis, "Volume")
        volume.Shape = solid
        doc.recompute()

        assert tree_children(volume, list(analysis.Group)) == [solid]

    def test_members_of_type_descends_into_the_folders(self, doc):
        analysis = make_analysis(doc)
        cap = make_cap(doc, analysis)
        doc.recompute()

        found = analysis.Proxy.members_of_type(analysis, "Audio::Cap")
        assert [o.Name for o in found] == [cap.Name]
