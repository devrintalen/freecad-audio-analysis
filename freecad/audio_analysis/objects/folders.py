"""Dedicated folders for the objects that arrive in quantity.

Most of an analysis is a graph and is filed as one: elements nest under the node they
connect, which turns the tree into an adjacency list (STRUCTURE.md §6.6). Caps and cavities
are not part of that graph. They are *geometry* -- a cap closes an opening, a cavity is the
air a boolean found -- and they arrive in numbers that swamp the topology the tree exists to
show. The two-way cup carries nineteen caps against about ten network objects, so the
analysis listed twenty-six things at its top level and the network was a minority of its own
tree. Folding them away leaves eight.

This follows the Assembly workbench, which gives joints, bills of materials, exploded views
and simulations one group apiece and finds each by a stable type tag rather than by its
label (`UtilsAssembly.getJointGroup`). The one thing that cannot be copied is the *kind* of
tag: Assembly registers `Assembly::JointGroup` as a C++ type, and this workbench must be
Python-only (CLAUDE.md). The Python equivalent is the one already used everywhere else
here -- a ``DocumentObjectGroupPython`` whose proxy carries a persisted :attr:`Type`, found
with :func:`is_audio_object`.

Two places this deliberately differs from Assembly, on the merits rather than by necessity:

- **A folder is never created empty.** Assembly makes `Joints` when the assembly is made,
  which is right when joints are the whole point of the container. Caps are optional here --
  air modelled directly as a solid needs none -- so an empty `Caps` on every analysis would
  be noise.
- **Creating one object tidies all of them.** Assembly needs no sweep because joints have
  only ever been created into the group. This arrangement arrived after documents already
  had caps loose in the analysis, so :func:`organise` sweeps the whole analysis and a
  half-organised tree never appears. Nothing moves on document *restore*: rearranging
  someone's tree as a side effect of opening the file, and marking it modified before they
  have touched it, is the worse surprise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from freecad.audio_analysis.objects.base import (
    AudioObject,
    attach_view_provider,
    is_audio_object,
)

#: Marker property used by the first version of this module, before the folders carried a
#: proxy. Recognised so that a document written by that one revision finds its existing
#: folder instead of quietly growing a second one beside it.
LEGACY_FOLDER_PROPERTY = "AudioFolder"


class AudioFolder(AudioObject):
    """A group holding one kind of object. Subclasses only set :attr:`Type`."""

    Type = "Audio::Folder"


class CapFolder(AudioFolder):
    Type = "Audio::CapFolder"


class CavityFolder(AudioFolder):
    Type = "Audio::CavityFolder"


@dataclass(frozen=True)
class FolderKind:
    """One sort of object that gets a folder of its own."""

    #: Internal object name, and the legacy marker value.
    key: str
    #: Default label. The user may rename the folder; lookup never depends on this.
    label: str
    #: :attr:`AudioObject.Type` of the objects collected.
    member_type: str
    #: :attr:`AudioObject.Type` of the folder itself.
    folder_type: str
    #: Proxy class for the folder.
    proxy: type
    #: View provider, as ``"module.path:ClassName"``.
    view_provider: str


CAPS = FolderKind(
    "Caps", "Caps", "Audio::Cap", CapFolder.Type, CapFolder,
    "freecad.audio_analysis.viewproviders.folders:ViewProviderCapFolder",
)
CAVITIES = FolderKind(
    "Cavities", "Cavities", "Audio::Cavity", CavityFolder.Type, CavityFolder,
    "freecad.audio_analysis.viewproviders.folders:ViewProviderCavityFolder",
)

#: Every kind that gets a folder, in the order the folders should appear.
FOLDER_KINDS: tuple[FolderKind, ...] = (CAVITIES, CAPS)

_FOLDER_TYPES = frozenset(kind.folder_type for kind in FOLDER_KINDS)


def _legacy_marker(obj: Any) -> str:
    marker = getattr(obj, LEGACY_FOLDER_PROPERTY, None)
    return marker if isinstance(marker, str) else ""


def is_folder(obj: Any, kind: FolderKind | None = None) -> bool:
    """Whether ``obj`` is one of our folders, optionally of a particular kind."""
    if kind is not None:
        return is_audio_object(obj, kind.folder_type) or _legacy_marker(obj) == kind.key
    proxy_type = getattr(getattr(obj, "Proxy", None), "Type", None)
    return proxy_type in _FOLDER_TYPES or _legacy_marker(obj) in {
        k.key for k in FOLDER_KINDS
    }


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

    group = analysis.Document.addObject("App::DocumentObjectGroupPython", kind.key)
    kind.proxy(group)
    attach_view_provider(group, kind.view_provider)
    group.Label = kind.label
    analysis.addObject(group)
    return group


def _move(folder: Any, obj: Any) -> bool:
    """Put ``obj`` in ``folder``, taking it out of wherever it was. True if it moved.

    Removing first matters: FreeCAD lets an object sit in two groups at once, and the
    result is an object drawn twice in the tree with nothing to say which copy is real.
    """
    if obj.Name == folder.Name:
        return False
    members = getattr(folder, "Group", []) or []
    if any(member.Name == obj.Name for member in members):
        return False
    for parent in list(obj.InList):
        if parent.Name == folder.Name or not hasattr(parent, "removeObject"):
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
            if is_audio_object(child, kind.member_type)
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
        if is_audio_object(child, kind.member_type)
    ]
    if folder is not None:
        found.extend(
            child
            for child in getattr(folder, "Group", ()) or ()
            if is_audio_object(child, kind.member_type)
        )
    return found
