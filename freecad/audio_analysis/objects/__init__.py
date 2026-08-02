"""Document objects for the Audio Analysis workbench.

Import factories from here rather than reaching into the submodules, so the public
surface stays stable as the object tree in STRUCTURE.md section 6.2 fills out.
"""

from freecad.audio_analysis.objects.analysis import AudioAnalysis, make_analysis
from freecad.audio_analysis.objects.base import (
    AudioObject,
    PropertySpec,
    find_active_analysis,
    is_audio_object,
)
from freecad.audio_analysis.objects.environment import Environment, make_environment

__all__ = [
    "AudioAnalysis",
    "AudioObject",
    "Environment",
    "PropertySpec",
    "find_active_analysis",
    "is_audio_object",
    "make_analysis",
    "make_environment",
]
