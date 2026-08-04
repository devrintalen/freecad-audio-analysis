"""Base class for every Audio Analysis document object.

All feature objects in this workbench follow FreeCAD's scripted-object pattern: a
``FeaturePython`` document object owns a Python ``Proxy`` instance that supplies its
properties and behaviour. This module holds the parts every one of them needs, so the
individual objects stay short enough to read in one screen.

Three things here are load-bearing:

* **Property declaration is idempotent.** Subclasses list their properties once, in
  :meth:`AudioObject.properties`, and the base class adds only what is missing. That runs
  on creation *and* on document restore, so a file saved by an older version of the
  workbench silently gains properties added since. Without this, opening an old model
  raises ``AttributeError`` deep inside a solve.
* **Nothing may recompute while the object is still restoring.** FreeCAD writes properties
  to a file in **alphabetical order** and restores them in that order, firing ``onChanged``
  for each. ``Proxy`` is restored in the middle of that sequence, so from then on
  ``onChanged`` dispatches into Python while the properties sorting after it do not exist
  yet. An ``Environment`` demonstrated this exactly: ``Density`` (index 1) had arrived, so a
  ``hasattr(obj, "Density")`` guard passed, but ``Temperature`` (index 12) had not, and
  opening a document produced three ``AttributeError`` tracebacks in the Report view.
  Guarding on one property is guarding on the alphabet. Use :func:`is_restoring`.
* **No GUI imports.** This module and everything under ``objects/`` must import cleanly
  with no ``FreeCADGui``, so the physics and document layers stay testable headlessly.
  View providers live in ``viewproviders/``.
"""

from __future__ import annotations

from typing import Any, Iterable, NamedTuple

import FreeCAD

#: Bumped when a change needs migration logic in :meth:`AudioObject.on_schema_upgrade`.
SCHEMA_VERSION = 1


class PropertySpec(NamedTuple):
    """Declarative description of one FreeCAD property."""

    type: str
    name: str
    group: str
    doc: str
    default: Any = None
    read_only: bool = False
    #: Enumeration values, for ``App::PropertyEnumeration`` only.
    enum: tuple[str, ...] | None = None


class AudioObject:
    """Common behaviour for Audio Analysis scripted objects.

    Subclasses set :attr:`Type` and implement :meth:`properties`.
    """

    #: Stable identifier persisted in the document. Never rename one of these without
    #: migration code -- it is how saved files find their proxy class again.
    Type = "Audio::Object"

    def __init__(self, obj: Any) -> None:
        obj.Proxy = self
        self.Type = self.Type
        self.ensure_properties(obj)

    # -- properties ------------------------------------------------------------------

    def properties(self) -> Iterable[PropertySpec]:
        """Return the properties this object owns. Override in subclasses."""
        return ()

    def ensure_properties(self, obj: Any) -> list[str]:
        """Add any declared property the object does not already have.

        Safe to call repeatedly; existing values are never overwritten. Returns the names
        of the properties actually added, which is how :meth:`onDocumentRestored` tells a
        file written by an older workbench from an up-to-date one without guessing.
        """
        added: list[str] = []
        for spec in self.properties():
            if hasattr(obj, spec.name):
                continue
            added.append(spec.name)
            obj.addProperty(spec.type, spec.name, spec.group, spec.doc)
            # Enumerations need their allowed values before a value can be assigned.
            if spec.enum:
                setattr(obj, spec.name, list(spec.enum))
            if spec.default is not None:
                setattr(obj, spec.name, spec.default)
            if spec.read_only:
                obj.setEditorMode(spec.name, 1)  # 1 == read-only in the property editor

        if not hasattr(obj, "SchemaVersion"):
            obj.addProperty(
                "App::PropertyInteger",
                "SchemaVersion",
                "Base",
                "Workbench schema version this object was written with",
            )
            obj.SchemaVersion = SCHEMA_VERSION
            obj.setEditorMode("SchemaVersion", 2)  # 2 == hidden
            added.append("SchemaVersion")
        return added

    # -- FreeCAD hooks ---------------------------------------------------------------

    def execute(self, obj: Any) -> None:
        """Recompute. Subclasses override; the default deliberately does nothing."""

    def onDocumentRestored(self, obj: Any) -> None:
        """Bring an object loaded from a saved file up to the current schema."""
        added = self.ensure_properties(obj)
        stored = getattr(obj, "SchemaVersion", 0)
        if stored < SCHEMA_VERSION:
            self.on_schema_upgrade(obj, stored)
            obj.SchemaVersion = SCHEMA_VERSION
        if added:
            self.on_properties_added(obj, added)

    def on_properties_added(self, obj: Any, names: list[str]) -> None:
        """Called after restore when properties had to be added to an older file.

        A property added this way has no value -- the file predates it -- so anything
        derived is stale until something recomputes it. Overridden by objects whose
        derived values are cheap to regenerate. Deliberately *not* called when the file
        already had every property: rewriting values that are already correct would mark
        an untouched document as modified merely for having been opened.
        """

    def on_schema_upgrade(self, obj: Any, from_version: int) -> None:
        """Migrate an object written by an older workbench version.

        Adding a property needs nothing here -- :meth:`ensure_properties` covers it.
        Override only when a value has to be *transformed*: units changed, a property
        was split in two, an enumeration gained or lost a member.
        """

    # -- serialisation ---------------------------------------------------------------
    # FreeCAD 1.0 replaced __getstate__/__setstate__ with dumps/loads for scripted
    # objects. Only the type tag is persisted; everything else lives in properties,
    # which FreeCAD saves itself.

    def dumps(self) -> tuple[str, int]:
        return (self.Type, SCHEMA_VERSION)

    def loads(self, state: Any) -> None:
        if isinstance(state, (tuple, list)) and state:
            self.Type = state[0]
        elif isinstance(state, str):  # tolerate an older single-string form
            self.Type = state


def is_restoring(obj: Any) -> bool:
    """True while FreeCAD is still reading ``obj`` back from a file.

    The guard every ``onChanged`` needs. Properties are restored in alphabetical order and
    ``Proxy`` sits in the middle of that order, so ``onChanged`` begins dispatching into
    Python well before the object is whole. Any handler that reads a property *other than
    the one it was handed* must wait for :meth:`AudioObject.onDocumentRestored`, which runs
    once everything is in place.
    """
    return any(str(flag).startswith("Restor") for flag in getattr(obj, "State", ()))


def attach_view_provider(obj: Any, factory_path: str) -> bool:
    """Attach a view provider to ``obj``, if and only if a GUI is actually running.

    ``factory_path`` is ``"module.path:ClassName"``.

    Testing ``import FreeCADGui`` is not enough: in a headless session the module imports
    successfully but no view providers exist, so ``obj.ViewObject`` is None.
    ``FreeCAD.GuiUp`` is the reliable signal, and the ViewObject is checked as well since
    some object types have none even in the GUI.

    Returns True if a view provider was attached.
    """
    if not getattr(FreeCAD, "GuiUp", False):
        return False
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return False

    module_name, _, class_name = factory_path.partition(":")
    try:
        module = __import__(module_name, fromlist=[class_name])
        getattr(module, class_name)(view_object)
    except Exception as exc:  # noqa: BLE001 -- a missing icon must not block modelling
        FreeCAD.Console.PrintWarning(
            f"Audio Analysis: could not attach view provider {factory_path}: {exc}\n"
        )
        return False
    return True


def is_audio_object(obj: Any, type_name: str | None = None) -> bool:
    """True if ``obj`` is one of ours, optionally of a specific :attr:`AudioObject.Type`.

    Checks the proxy's persisted ``Type`` string rather than the Python class, because
    after a document reload the class identity may differ while the tag is stable.
    """
    proxy = getattr(obj, "Proxy", None)
    actual = getattr(proxy, "Type", None)
    if not isinstance(actual, str) or not actual.startswith("Audio::"):
        return False
    return actual == type_name if type_name else True


def find_active_analysis(doc: Any = None) -> Any | None:
    """Return the analysis an action should target, or None.

    Prefers whatever the GUI has marked active; falls back to the sole analysis in the
    document when there is exactly one, which is the common case and saves the user a
    click. Deliberately returns None when the choice is ambiguous rather than guessing.
    """
    from freecad.audio_analysis.objects.analysis import AudioAnalysis

    if doc is None:
        doc = FreeCAD.ActiveDocument
    if doc is None:
        return None

    try:  # GUI selection is a hint only; absent in headless use.
        import FreeCADGui

        active = getattr(FreeCADGui, "ActiveDocument", None)
        marked = getattr(active, "ActiveView", None)
        if marked is not None and hasattr(marked, "getActiveObject"):
            candidate = marked.getActiveObject("AudioAnalysis")
            if candidate is not None and is_audio_object(candidate, AudioAnalysis.Type):
                return candidate
    except (ImportError, AttributeError):
        pass

    analyses = [o for o in doc.Objects if is_audio_object(o, AudioAnalysis.Type)]
    return analyses[0] if len(analyses) == 1 else None
