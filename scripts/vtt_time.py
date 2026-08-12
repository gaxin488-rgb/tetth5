"""Helpers for displaying numeric cue times as WebVTT timecodes."""

from __future__ import annotations

from typing import Any


def format_timestamp(value: Any) -> str:
    """Return ``HH:MM:SS.mmm`` without changing the source numeric time."""
    try:
        milliseconds_total = int(round(float(value) * 1000))
    except (TypeError, ValueError):
        return ""
    if milliseconds_total < 0:
        return ""
    hours, remainder = divmod(milliseconds_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def format_interval(start: Any, end: Any) -> str:
    """Return the WebVTT cue timing line used in reports."""
    left = format_timestamp(start)
    right = format_timestamp(end)
    return f"{left} --> {right}" if left and right else ""
