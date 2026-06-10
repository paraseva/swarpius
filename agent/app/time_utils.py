"""Small time-formatting helpers shared across context providers.

Lives at the ``app/`` root so it can be imported from any subpackage
without crossing layering boundaries (e.g. ``app/roon/`` importing
from ``app/coordinator/`` and vice versa).
"""

from __future__ import annotations


def format_relative_time(seconds_ago: float) -> str:
    """Format an elapsed duration as a compact "N ago" phrase.

    Bucket boundaries: under a minute → "just now"; under an hour →
    "N min ago"; under a day → "N hr ago"; otherwise "N day(s) ago".
    Negative inputs (which can arise from clock jitter) are clamped
    to zero so the output is always non-negative.
    """
    seconds_ago = max(0.0, seconds_ago)
    if seconds_ago < 60:
        return "just now"
    if seconds_ago < 3600:
        return f"{int(seconds_ago // 60)} min ago"
    if seconds_ago < 86400:
        return f"{int(seconds_ago // 3600)} hr ago"
    days = int(seconds_ago // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"
