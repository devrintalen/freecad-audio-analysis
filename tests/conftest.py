"""Test configuration.

Two suites live side by side:

* Pure Python (``test_air.py``, ``test_units.py``, ``test_crossover.py``, the benchmarks)
  -- runs in any interpreter, no FreeCAD needed.
* Integration (``test_freecad_integration.py`` and the document-object tests) -- needs
  FreeCAD's bindings, which are not on the default ``sys.path``. They are located here so
  ``python3 -m pytest`` just works from the repository root.

If FreeCAD cannot be found the integration tests skip rather than fail: the pure suite is
still worth running, and CI may not have FreeCAD available.

The import-path juggling lives in ``scripts/devpath`` because the benchmark runner and the
example scripts need exactly the same thing.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.devpath import setup  # noqa: E402

setup()
