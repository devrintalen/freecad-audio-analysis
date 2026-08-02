"""Base class for every Audio Analysis document object.

All feature objects in this workbench follow FreeCAD's scripted-object pattern: a
``FeaturePython`` document object owns a Python ``Proxy`` instance that supplies its
properties and behaviour. This module holds the parts every one of them needs, so the
individual objects stay short enough to read in one screen.

Two things here are load-bearing:

* **Property declaration is idempotent.** Subclasses list their properties once, in
  :meth:`AudioObject.properties`, and the base class adds only what is missing. That runs
  on creation *and* on document restore, so a file saved by an older version of the
  workbench silently gains properties added since. Without this, opening an old model
  raises ``AttributeError`` deep inside a solve.
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

    def ensure_properties(self, obj: Any) -> None:
        """Add any declared property the object does not already have.

        Safe to call repeatedly; existing values are never overwritten.
        """
        for spec in self.properties():
            if hasattr(obj, spec.name):
                continue
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

    # -- FreeCAD hooks ---------------------------------------------------------------

    def execute(self, obj: Any) -> None:
        """Recompute. Subclasses override; the default deliberately does nothing."""

    def onDocumentRestored(self, obj: Any) -> None:
        """Bring an object loaded from a saved file up to the current schema."""
        self.ensure_properties(obj)
        stored = getattr(obj, "SchemaVersion", 0)
        if stored < SCHEMA_VERSION:
            self.on_schema_upgrade(obj, stored)
            obj.SchemaVersion = SCHEMA_VERSION

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
