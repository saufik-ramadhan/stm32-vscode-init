"""Toolchain profile registry.

To support another MCU vendor: add profiles/<name>.py implementing
ToolchainProfile (see profiles/base.py), then list it in PROFILES below.
core.py never needs to change.
"""
from profiles.stm32cubeclt import STM32CubeCLTProfile

PROFILES = {
    "stm32cubeclt": STM32CubeCLTProfile,
}


def detect_profile(project_dir):
    """Return an instance of the highest-confidence profile for project_dir,
    or None if nothing matched."""
    best = None
    best_score = 0.0
    for cls in PROFILES.values():
        profile = cls()
        score = profile.detect(project_dir)
        if score > best_score:
            best, best_score = profile, score
    return best if best_score > 0.0 else None
