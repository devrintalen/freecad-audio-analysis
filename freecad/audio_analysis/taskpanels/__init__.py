"""Task panels for the Audio Analysis workbench.

Everything in this package imports ``FreeCADGui`` and Qt, so -- exactly as with
``viewproviders/`` -- nothing under ``objects/`` or ``physics/`` may import it at module
level. Commands import from here lazily, inside the method that opens the panel, so the
document and physics layers stay importable in a headless interpreter (CLAUDE.md).
"""
