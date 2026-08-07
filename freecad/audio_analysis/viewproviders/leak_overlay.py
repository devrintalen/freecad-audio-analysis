"""Drawing the escape route in the 3D view.

The trace's text report gives a coordinate, and a coordinate inside a hundred-millimetre
assembly is not somewhere anyone can look. The route matters more than the number anyway:
knowing the leak is 1.5 mm across says how bad it is, but seeing the line come out of the
back volume, run round an annulus and break through the wall says *what it is*.

Drawn as a transient Coin3D overlay rather than a document object. A `Part::Feature`
polyline would be simpler, but the panel creates its cavity inside a transaction that
Cancel aborts, so a path object would either vanish with the cavity on Cancel or be left
behind in the document on OK -- and a leftover diagnostic polyline in a saved model is
exactly the sort of thing that gets mistaken for geometry later. The overlay belongs to
the view, not the document, and goes when the panel does.

**Colour carries the clearance.** The line runs red where the void pinches down and pale
blue where it is open, which puts the answer to "where does it neck down" in the picture
instead of in a table of coordinates. The ramp is warm-to-cool with luminance rising along
it, so it survives being read by someone who cannot separate red from green, and it still
reads in a greyscale screenshot.

`SoAnnotation` is what makes it visible at all: the route is *inside* the model by
construction, and ordinary geometry would be hidden by the parts around it even with the
preview's translucency.
"""

from __future__ import annotations

from typing import Any, Sequence

#: Colour of the tightest point on the route.
TIGHT_COLOUR = (0.86, 0.16, 0.16)
#: Colour halfway along the clearance range.
MID_COLOUR = (0.95, 0.66, 0.18)
#: Colour where the route is at its most open.
#:
#: Light enough that relative luminance still *rises* past the amber midpoint -- 0.31, 0.69,
#: 0.80 across the three stops. A darker blue looks better in isolation and breaks the ramp:
#: luminance peaks in the middle, so the two ends become indistinguishable in greyscale and
#: for anyone reading the line by brightness rather than hue.
OPEN_COLOUR = (0.60, 0.84, 0.99)

#: Line weight. Heavy, because the route is seen through dimmed parts rather than against
#: a clear background.
LINE_WIDTH = 6.0


def _mix(one: Sequence[float], other: Sequence[float], t: float) -> tuple[float, ...]:
    return tuple(a + (b - a) * t for a, b in zip(one, other))


def colour_for(clearance: float, tightest: float, widest: float) -> tuple[float, ...]:
    """Where ``clearance`` sits on the ramp, as an RGB triple.

    A degenerate range -- every point equally clear, which happens on a short path through
    a uniform channel -- collapses to the tight end rather than dividing by zero. That is
    the safe direction: a route drawn entirely red is read as "look here", and a route
    drawn entirely blue is read as "nothing to see".
    """
    span = widest - tightest
    t = 0.0 if span <= 1e-9 else max(0.0, min(1.0, (clearance - tightest) / span))
    if t < 0.5:
        return _mix(TIGHT_COLOUR, MID_COLOUR, t * 2.0)
    return _mix(MID_COLOUR, OPEN_COLOUR, (t - 0.5) * 2.0)


class LeakOverlay:
    """The escape route, drawn into the active 3D view until it is cleared.

    Every failure here is swallowed: an overlay that will not draw is a cosmetic problem,
    and it must never stop a cavity being extracted or a panel being closed.
    """

    def __init__(self) -> None:
        self._node: Any = None
        self._scene: Any = None

    def show(self, result: Any) -> bool:
        """Draw ``result``'s path. Returns whether anything was drawn."""
        self.clear()
        if result is None or not getattr(result, "path", None):
            return False

        try:
            from pivy import coin
            import FreeCADGui
        except ImportError:
            return False

        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            scene = view.getSceneGraph()
        except Exception:  # noqa: BLE001 -- no view, or one that has gone away
            return False

        try:
            node = self._build(coin, result)
            scene.addChild(node)
        except Exception:  # noqa: BLE001 -- Coin APIs vary between builds
            return False

        self._node, self._scene = node, scene
        return True

    @staticmethod
    def _build(coin: Any, result: Any) -> Any:
        points = [(x, y, z) for x, y, z, _ in result.path]
        clearances = [c for *_, c in result.path]
        tightest, widest = min(clearances), max(clearances)
        colours = [colour_for(c, tightest, widest) for c in clearances]

        # SoAnnotation draws after everything else and ignores what is in front of it,
        # which is the only way a route buried inside the parts is visible at all.
        node = coin.SoAnnotation()

        style = coin.SoDrawStyle()
        style.lineWidth = LINE_WIDTH
        node.addChild(style)

        material = coin.SoMaterial()
        material.diffuseColor.setValues(0, len(colours), colours)
        node.addChild(material)

        binding = coin.SoMaterialBinding()
        binding.value = coin.SoMaterialBinding.PER_VERTEX
        node.addChild(binding)

        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(points), points)
        node.addChild(coords)

        lines = coin.SoLineSet()
        lines.numVertices.setValue(len(points))
        node.addChild(lines)

        node.addChild(LeakOverlay._bottleneck(coin, result))
        return node

    @staticmethod
    def _bottleneck(coin: Any, result: Any) -> Any:
        """A sphere at the tightest point, drawn at the clearance it actually measures.

        Sized honestly rather than for visibility: the sphere *is* the room available, so
        on a real leak it is a speck. A screen-space marker is added on top so the speck
        can still be found, since a marker keeps its size however far out the view zooms.
        """
        group = coin.SoSeparator()

        move = coin.SoTranslation()
        move.translation = tuple(result.point)
        group.addChild(move)

        material = coin.SoMaterial()
        material.diffuseColor = TIGHT_COLOUR
        material.transparency = 0.3
        group.addChild(material)

        sphere = coin.SoSphere()
        sphere.radius = max(float(result.clearance_mm), 1e-3)
        group.addChild(sphere)

        marker = coin.SoMarkerSet()
        try:
            marker.markerIndex = coin.SoMarkerSet.CROSS_9_9
        except Exception:  # noqa: BLE001 -- marker enums differ between Coin builds
            pass
        marker.numPoints = 1
        group.addChild(coin.SoCoordinate3())  # a single point at the local origin
        group.addChild(marker)
        return group

    def clear(self) -> None:
        """Take the overlay out of the view. Safe to call when nothing is drawn."""
        if self._node is None or self._scene is None:
            return
        try:
            self._scene.removeChild(self._node)
        except Exception:  # noqa: BLE001 -- the view may already have gone
            pass
        self._node = self._scene = None
