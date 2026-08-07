"""The escape route drawn in the 3D view.

Only the parts that survive without a GUI are tested here: the colour ramp, and the
contract that nothing is drawn when there is nothing to draw. The Coin3D scene graph needs
a running view and is exercised by using it.
"""

from __future__ import annotations

import pytest

from freecad.audio_analysis.viewproviders import leak_overlay
from freecad.audio_analysis.viewproviders.leak_overlay import (
    LeakOverlay,
    OPEN_COLOUR,
    TIGHT_COLOUR,
    colour_for,
)


class TestColourRamp:
    def test_the_tightest_point_is_the_tight_colour(self):
        assert colour_for(0.75, 0.75, 15.0) == pytest.approx(TIGHT_COLOUR)

    def test_the_most_open_point_is_the_open_colour(self):
        assert colour_for(15.0, 0.75, 15.0) == pytest.approx(OPEN_COLOUR)

    def test_it_runs_warm_to_cool_without_doubling_back(self):
        """Blue must rise all the way, so the ordering is never ambiguous.

        Red is deliberately *not* asserted monotonic: it rises into the amber midpoint
        before falling away, as every warm-to-cool ramp's red channel does. Requiring it
        would be testing the implementation rather than the property that matters.
        """
        blues = [b for _r, _g, b in (colour_for(c, 0.0, 10.0) for c in range(11))]
        assert blues == sorted(blues)

    def test_luminance_rises_along_the_ramp(self):
        """So it still reads for a red/green-blind viewer, and in a greyscale screenshot."""
        def luminance(rgb):
            r, g, b = rgb
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        values = [luminance(colour_for(c, 0.0, 10.0)) for c in range(11)]
        assert values == sorted(values)

    def test_a_uniform_route_collapses_to_the_tight_end(self):
        """Rather than dividing by zero -- and red is the safe direction to fail in,
        because it reads as 'look here' rather than 'nothing to see'."""
        assert colour_for(2.0, 2.0, 2.0) == pytest.approx(TIGHT_COLOUR)

    def test_clearances_outside_the_range_are_clamped(self):
        assert colour_for(-5.0, 0.0, 10.0) == pytest.approx(TIGHT_COLOUR)
        assert colour_for(99.0, 0.0, 10.0) == pytest.approx(OPEN_COLOUR)


class TestDrawingNothing:
    def test_nothing_is_drawn_for_a_missing_result(self):
        """`find_escape_path` returns None for a sealed cavity, and that must not draw."""
        assert LeakOverlay().show(None) is False

    def test_nothing_is_drawn_for_an_empty_path(self):
        class Empty:
            path: list = []

        assert LeakOverlay().show(Empty()) is False

    def test_clearing_an_overlay_that_never_drew_is_safe(self):
        """`_stop` clears unconditionally on every panel close, traced or not."""
        overlay = LeakOverlay()
        overlay.clear()
        overlay.clear()

    def test_show_reports_failure_rather_than_raising_without_a_view(self):
        """Headless, or with no active view: a cosmetic feature must not break the panel."""

        class Route:
            path = [(0.0, 0.0, 0.0, 1.0), (1.0, 0.0, 0.0, 2.0)]
            point = (0.0, 0.0, 0.0)
            clearance_mm = 1.0

        assert LeakOverlay().show(Route()) is False


def test_the_overlay_module_is_importable_without_a_gui():
    """It lives under viewproviders/ and may import FreeCADGui only when it draws."""
    assert leak_overlay.LINE_WIDTH > 0
