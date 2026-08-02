"""FreeCAD console-mode entry point.

Runs in both GUI and headless sessions, before InitGui.py. Nothing needs to happen here:
the workbench registers its commands lazily when activated, and the document objects are
imported on demand. Kept as a deliberate no-op so the file's absence is not mistaken for
an installation problem.
"""
