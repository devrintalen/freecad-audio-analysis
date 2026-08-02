"""Command registration helpers.

FreeCAD commands are classes with ``GetResources``/``Activated``/``IsActive`` registered
under a string name. This module supplies the boilerplate and a consistent error policy:
a command that fails should report a readable message to the user and log the traceback,
never let an exception escape into FreeCAD's event loop.
"""

from __future__ import annotations

import traceback
from typing import Any

import FreeCAD
import FreeCADGui

from freecad.audio_analysis import icon

#: Prefix for every command name, keeping the global command namespace tidy.
COMMAND_PREFIX = "Audio_"


class AudioCommand:
    """Base class for workbench commands."""

    #: Command name suffix; the registered name is ``COMMAND_PREFIX + Name``.
    Name = ""
    MenuText = ""
    ToolTip = ""
    IconName = "AudioAnalysis"

    @property
    def command_name(self) -> str:
        return f"{COMMAND_PREFIX}{self.Name}"

    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": icon(self.IconName),
            "MenuText": self.MenuText,
            "ToolTip": self.ToolTip,
        }

    def IsActive(self) -> bool:
        """Default: available whenever a document is open."""
        return FreeCAD.ActiveDocument is not None

    def Activated(self) -> None:
        """FreeCAD entry point. Delegates to :meth:`run` with error handling."""
        try:
            self.run()
        except Exception as exc:  # noqa: BLE001 -- boundary with FreeCAD's event loop
            FreeCAD.Console.PrintError(f"Audio Analysis: {self.MenuText} failed: {exc}\n")
            FreeCAD.Console.PrintLog(traceback.format_exc())

    def run(self) -> None:
        """Do the work. Override in subclasses."""
        raise NotImplementedError


def register(command: AudioCommand) -> str:
    """Register a command instance with FreeCAD and return its name."""
    FreeCADGui.addCommand(command.command_name, command)
    return command.command_name


def transaction(name: str):
    """Context manager wrapping document changes in an undo transaction.

    Without this, a command that creates three objects needs three undos, and a command
    that fails halfway leaves debris behind.
    """

    class _Transaction:
        def __enter__(self) -> Any:
            self.doc = FreeCAD.ActiveDocument
            if self.doc is not None:
                self.doc.openTransaction(name)
            return self.doc

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            if self.doc is None:
                return False
            if exc_type is None:
                self.doc.commitTransaction()
                self.doc.recompute()
            else:
                self.doc.abortTransaction()
            return False  # Never swallow; Activated() reports it.

    return _Transaction()
