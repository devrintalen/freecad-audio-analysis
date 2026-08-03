"""The crossover object: which drivers play which frequencies.

A two-way is not two one-ways. Without a filter every driver in an analysis is fed the
same voltage at every frequency, so a tweeter is asked to reproduce bass it cannot
survive and the two drivers fight each other through the whole band. The crossover is
what makes a multi-driver design a system, and it is the last piece Tier 1 needs.

One :class:`CrossoverFilter` describes one branch and names the drivers it feeds. Several
drivers may share a branch -- two woofers wired in parallel behind one inductor is a real
arrangement -- but a driver belongs to at most one, which the checks enforce.

**Active or passive** is the choice that matters most, and it is not cosmetic. See
:mod:`freecad.audio_analysis.physics.crossover`: a passive ladder sits between the
amplifier and the coil, so it changes the driver's damping as well as its level, and its
component values interact with an impedance curve that is nothing like the flat resistance
the textbook values assume.
"""

from __future__ import annotations

from typing import Any, Iterable

import FreeCAD

from freecad.audio_analysis.objects.base import PropertySpec, attach_view_provider
from freecad.audio_analysis.objects.network_objects import NetworkObject, quantity
from freecad.audio_analysis.physics import crossover as physics

GROUP_FILTER = "Filter"
GROUP_LEVEL = "Level"

#: How the filter is built. The enumeration order sets the default.
REALISATIONS = ("Active", "Passive")


class CrossoverFilter(NetworkObject):
    """One branch of a crossover, feeding one or more drivers."""

    Type = "Audio::Crossover"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec(
                "App::PropertyEnumeration", "Response", GROUP_FILTER,
                "Which side of the crossover frequency this branch passes. Bypass still "
                "applies gain and delay, which is how a tweeter gets padded down without "
                "being filtered.",
                enum=physics.RESPONSES, default="Bypass",
            ),
            PropertySpec(
                "App::PropertyEnumeration", "Alignment", GROUP_FILTER,
                "Filter shape. Linkwitz-Riley is 6 dB down at the corner so a "
                "complementary pair sums flat, which is why it is the usual multi-way "
                "choice; Butterworth is 3 dB down and sums with a bump. Bessel trades "
                "flatness for constant group delay.",
                enum=physics.ALIGNMENTS, default="Linkwitz-Riley",
            ),
            PropertySpec(
                "App::PropertyInteger", "Order", GROUP_FILTER,
                "Slope, in units of 6 dB per octave. Linkwitz-Riley needs an even order.",
                default=2,
            ),
            PropertySpec(
                "App::PropertyFrequency", "Frequency", GROUP_FILTER,
                "Corner frequency. Both branches of a pair normally use the same value.",
                default=quantity(2000.0, "Hz"),
            ),
            PropertySpec(
                "App::PropertyEnumeration", "Realisation", GROUP_FILTER,
                "Active means a line-level filter and one power amplifier per driver: the "
                "response is exact and delay is available. Passive means an inductor and "
                "capacitor ladder in the signal path, which also changes how the driver "
                "is damped.",
                enum=REALISATIONS, default="Active",
            ),
            PropertySpec(
                "App::PropertyFloat", "NominalImpedance", GROUP_FILTER,
                "Load the passive component values are computed against, ohms. Usually "
                "the driver's Re. The response you actually get will differ, because a "
                "driver's impedance is not flat -- that difference is the reason to "
                "simulate rather than trust the table.",
                default=32.0,
            ),
            PropertySpec(
                "App::PropertyFloat", "Gain", GROUP_LEVEL,
                "Level trim, dB. Almost always negative: the more sensitive driver is "
                "padded down to meet the other. A passive branch realises this as an "
                "L-pad and cannot amplify.",
                default=0.0,
            ),
            PropertySpec(
                "App::PropertyTime", "Delay", GROUP_LEVEL,
                "Signal delay, to line up drivers that are not physically flush. "
                "Available only on an active branch.",
                default=quantity(0.0, "s"),
            ),
            PropertySpec(
                "App::PropertyLinkList", "Drivers", "Connections",
                "Drivers fed by this branch. A driver may appear in only one crossover.",
            ),
            PropertySpec(
                "App::PropertyString", "Components", "Derived",
                "What this branch realises as", default="", read_only=True,
            ),
        )

    # -- behaviour ---------------------------------------------------------------------

    @staticmethod
    def is_passive(obj: Any) -> bool:
        return str(obj.Realisation) == "Passive"

    def filter(self, obj: Any) -> physics.Filter:
        """Build the physics-layer filter this object describes.

        Raises :class:`~freecad.audio_analysis.physics.crossover.CrossoverError` for a
        combination that has no realisation, such as an odd-order Linkwitz-Riley.
        """
        return physics.make_filter(
            response=str(obj.Response),
            alignment=str(obj.Alignment),
            order=int(obj.Order),
            frequency=obj.Frequency.getValueAs("Hz").Value,
            gain_db=obj.Gain,
            delay=obj.Delay.getValueAs("s").Value,
            passive=self.is_passive(obj),
            impedance=obj.NominalImpedance,
        )

    def execute(self, obj: Any) -> None:
        """Refresh the human-readable summary of what this branch is.

        For a passive branch this is where the component values appear -- the numbers a
        user would actually buy. Keeping them derived rather than typed means changing the
        crossover frequency changes the parts list.
        """
        try:
            obj.Components = self.filter(obj).describe()
        except physics.CrossoverError as exc:
            obj.Components = f"unrealisable: {exc}"


def make_crossover(doc: Any, analysis: Any = None, name: str = "Crossover") -> Any:
    obj = doc.addObject("App::FeaturePython", name)
    proxy = CrossoverFilter(obj)
    proxy.execute(obj)
    attach_view_provider(
        obj, "freecad.audio_analysis.viewproviders.network:ViewProviderNetworkObject"
    )
    if analysis is not None:
        analysis.addObject(obj)
    return obj


def crossover_for(analysis: Any, driver: Any) -> Any | None:
    """The crossover branch feeding ``driver``, or None if it is driven directly."""
    from freecad.audio_analysis.objects.base import is_audio_object

    for obj in getattr(analysis, "Group", []) or []:
        if not is_audio_object(obj, CrossoverFilter.Type):
            continue
        if any(d is not None and d.Name == driver.Name for d in obj.Drivers):
            return obj
    return None
