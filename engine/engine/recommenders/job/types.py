"""Job-recommender-specific typed results."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ...analysis_core.types import ResourceEvidence


@dataclass
class Peer:
    workload: str
    shared_seasonality: bool
    savings_amount: Optional[float]
    to_target: str
    note: str


@dataclass
class WorkloadRecommendation:
    """One recommendation card + its evidence."""
    workload_uid: str
    workload_kind: str
    workload_name: str
    namespace: str
    from_type: str
    to_target: str
    cadence: str
    run_time: str
    duration: str
    savings_amount: Optional[float]
    savings_currency: str
    savings_period: str
    confidence: str
    summary_text: str
    evidence: list[ResourceEvidence] = field(default_factory=list)
    peers: list[Peer] = field(default_factory=list)
