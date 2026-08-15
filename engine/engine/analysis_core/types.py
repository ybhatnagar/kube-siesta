"""Shared typed results used by every recommender head.

Job-specific types (WorkloadRecommendation, Peer) live under
`recommenders/job/types.py`; maintenance-specific types will land under
`recommenders/maintenance/` in a later milestone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Interval:
    """A contiguous active window, as timestamps."""
    start: datetime
    end: datetime


@dataclass
class ResourceEvidence:
    """Per-resource evidence — maps 1:1 to a recommendation_evidence row."""
    resource: str
    jump_pct: float
    active_idle_ratio: float
    period_hours: float
    active_duration_min: float
    overlap_pct: float
    trend_value: float
    eps_min: float
    eps_max: float
    active_windows: list[Interval] = field(default_factory=list)
    series: list[dict] = field(default_factory=list)  # downsampled points + overlay
