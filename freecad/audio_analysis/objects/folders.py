"""Dedicated folders for the objects that arrive in quantity.

Most of an analysis is a graph and is filed as one: elements nest under the node they
connect, which turns the tree into an adjacency list (STRUCTURE.md §6.6). Caps and cavities
are not part of that graph. They are *geometry* — a cap closes an opening, a cavity is the
air a boolean found — and they arrive in numbers that swamp the topology the tree exists to
show. The two-way cup carries nineteen caps against about ten network objects, so the
network became a minority of its own analysis.

So they go in folders, the way an Assembly files its joints: one `App::DocumentObjectGroup`
per kind, created on demand and never when it would be empty.

**Found by a marker, not by a label.** The folder carries a hidden ``AudioFolder`` property
naming what it collects. Matching on the label instead would break the moment anyone
renamed "Caps" to something they preferred, and would silently produce a second folder on
the next creation — the failure would be a duplicate rather than an error, which is the
kind that survives a long time.

**Tidying is not confined to the new object.** Creating any cap or cavity runs
:func:`organise` over the whole analysis, so caps that predate this arrangement are swept
in as well. The alternative — filing only what is being created — leaves a document that is
half organised and looks broken. Nothing is moved on document restore: rearranging someone's
tree as a side effect of opening the file is a surprise, and marks the document modified
before they have touched it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from freecad.audio_analysis.objects.base import is_audio_object

#: Hidden property naming what a folder collects. The folder is found by this.
FOLDER_PROPERTY = "AudioFolder"


@dataclass(frozen=True)
class FolderKind:
    """One sort of object that gets a folder of its own."""

    #: Internal name, and the marker written into ``AudioFolder``.
    key: str
    #: Default label. The user may rename the folder; lookup does not depend on this.
    label: str
    #: The :attr:`AudioObject.Type` of the objects collected.
    type_name: str


CAPS = FolderKind("Caps", "Caps", "Audio::Cap")
CAVITIES = FolderKind("Cavities", "Cavities", "Audio::Cavity")

#: Every kind that gets a folder, in the order the folders should appear.
FOLDER_KINDS: tuple[FolderKind, ...] = (CAVITIES, CAPS)


def is_folder(obj: Any, kind: FolderKind | None = None) -> bool:
    """Whether ``obj`` is one of our folders, optionally of a particular kind."""
    marker = getattr(obj, FOLDER_PROPERTY, None)
    if not isinstance(marker, str) or not marker:
        return False
    return marker == kind.key if kind is not None else True


def find_folder(analysis: Any, kind: FolderKind) -> Any | None:
    """The analysis's folder for ``kind``, or ``None`` if it has none yet."""
    for child in getattr(analysis, "Group", ()) or ():
        if is_folder(child, kind):
            return child
    return None


def ensure_folder(analysis: Any, kind: FolderKind) -> Any:
    """The analysis's folder for ``kind``, created if it does not exist."""
    existing = find_folder(analysis, kind)
    if existing is not None:
        return existing

    group = analysis.Document.addObject("App::DocumentObjectGroup", kind.key)
    group.addProperty(
        "App::PropertyString",
        FOLDER_PROPERTY,
        "Audio",
        "What this folder collects. The workbench finds the folder by this rather than "
        "by its label, so the folder can be renamed freely.",
    )
    setattr(group, FOLDER_PROPERTY, kind.key)
    try:
        # Read-only rather than hidden: it explains why the folder behaves as it does,
        # and a property nobody can see is a property nobody can debug.
        group.setEditorMode(FOLDER_PROPERTY, 1)
    except Exception:  # noqa: BLE001 -- editor modes vary between FreeCAD builds
        pass
    group.Label = kind.label
    analysis.addObject(group)
    return group


def _move(folder: Any, obj: Any) -> bool:
    """Put ``obj`` in ``folder``, taking it out of wherever it was. True if it moved.

    Removing first matters: FreeCAD lets an object sit in two groups at once, and the
    result is an object drawn twice in the tree with no indication which copy is real.
    """
    if obj.Name == folder.Name:
        return False
    members = getattr(folder, "Group", []) or []
    if any(member.Name == obj.Name for member in members):
        return False
    for parent in list(obj.InList):
        if parent.Name == folder.Name:
            continue
        if not hasattr(parent, "removeObject"):
            continue
        group = getattr(parent, "Group", None)
        if group and any(member.Name == obj.Name for member in group):
            parent.removeObject(obj)
    folder.addObject(obj)
    return True


def organise(analysis: Any, kinds: Sequence[FolderKind] = FOLDER_KINDS) -> list[Any]:
    """File every loose cap and cavity in ``analysis`` into its folder.

    Returns the objects that moved. A folder is created only for a kind that has something
    to put in it, so an analysis with no caps never grows an empty ``Caps``.
    """
    if analysis is None:
        return []
    moved: list[Any] = []
    for kind in kinds:
        loose = [
            child
            for child in list(getattr(analysis, "Group", ()) or ())
            if is_audio_object(child, kind.type_name)
        ]
        if not loose:
            continue
        folder = ensure_folder(analysis, kind)
        for item in loose:
            if _move(folder, item):
                moved.append(item)
    return moved


def members(analysis: Any, kind: FolderKind) -> list[Any]:
    """Every object of ``kind`` in ``analysis``, whether foldered or still loose.

    Callers should use this rather than filtering ``analysis.Group``: an object may be in
    the folder, or loose because it predates the folder, and which one is an accident of
    when it was made.
    """
    folder = find_folder(analysis, kind)
    found = [
        child
        for child in getattr(analysis, "Group", ()) or ()
        if is_audio_object(child, kind.type_name)
    ]
    if folder is not None:
        found.extend(
            child
            for child in getattr(folder, "Group", ()) or ()
            if is_audio_object(child, kind.type_name)
        )
    return found
