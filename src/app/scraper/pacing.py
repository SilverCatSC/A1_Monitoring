from __future__ import annotations

import random


def choose_pause(min_seconds: float, max_seconds: float) -> float:
    """Choose a bounded pause; invalid reversed bounds fail safe to the larger value."""
    lower = max(0.0, float(min_seconds))
    upper = max(lower, float(max_seconds))
    if upper == 0:
        return 0.0
    return random.uniform(lower, upper)
