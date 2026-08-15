"""Maintenance recommender: given a workload that must go down for duration L
before deadline D, find the future time window causing the least collective
impact across the target and its upstream callers (docs/07).
"""

from .runner import RunResult, run_maintenance_analysis
from .types import (
    ImpactedApp,
    MaintenanceConfig,
    MaintenanceEvidence,
    MaintenanceResult,
    WorkloadForecast,
)

__all__ = [
    "RunResult",
    "run_maintenance_analysis",
    "MaintenanceConfig",
    "MaintenanceResult",
    "ImpactedApp",
    "MaintenanceEvidence",
    "WorkloadForecast",
]
