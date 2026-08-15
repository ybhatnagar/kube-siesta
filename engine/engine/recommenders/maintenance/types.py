"""Maintenance-specific typed results (docs/07 §1).

Kept dict-serializable at the boundaries so `StateStore.insert_maintenance_result`
(which is dict-typed for now — see M2) can consume them without extra glue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class MaintenanceConfig:
    """User-supplied inputs for a maintenance run."""
    target_workload_uid: str
    duration_minutes: float
    deadline: datetime           # absolute UTC
    resample_freq: str = "1h"    # future-index step (default aligns with EngineConfig)


@dataclass
class ImpactedApp:
    """One upstream caller carried through the multi-app aggregation."""
    workload_uid: str
    workload_kind: Optional[str] = None
    workload_name: Optional[str] = None
    namespace: Optional[str] = None
    period_hours: Optional[float] = None       # None → aperiodic (treated as always-active)
    active_fraction: Optional[float] = None    # forecast horizon fraction projected active
    impact_score: float = 0.0                  # samples in the chosen window where it's active
    note: str = ""


@dataclass
class MaintenanceEvidence:
    """Forecast trace + projected active windows for one workload (target or dep)."""
    workload_uid: str
    resource: str
    forecast_series: list = field(default_factory=list)   # [{ts, value}] downsampled
    active_windows: list = field(default_factory=list)    # [{start, end}]


@dataclass
class WorkloadForecast:
    """Projection of one workload's active mask onto the future index.

    Aperiodic workloads carry `is_periodic=False` and an all-True mask so the
    scoring stage treats them as always-active — the confirmed pessimistic
    default (see docs/07 discussion + real-deployment memory).
    """
    workload_uid: str
    period_hours: Optional[float]
    active_forecast: object      # pd.Series[bool] indexed on the future grid
    is_periodic: bool
    note: str = ""


@dataclass
class MaintenanceResult:
    """One maintenance recommendation card + its evidence."""
    maintenance_for_uid: str
    workload_kind: str
    workload_name: str
    namespace: str
    recommended_start: datetime
    recommended_end: datetime
    duration_min: float
    deadline: datetime
    impact_score: float
    confidence: str              # 'high' | 'medium' | 'low'
    summary_text: str
    impacted_apps: list = field(default_factory=list)   # list[ImpactedApp]
    evidence: list = field(default_factory=list)         # list[MaintenanceEvidence]

    def to_row(self) -> dict:
        """Shape expected by StateStore.insert_maintenance_result."""
        return {
            "maintenance_for_uid": self.maintenance_for_uid,
            "workload_kind": self.workload_kind,
            "workload_name": self.workload_name,
            "namespace": self.namespace,
            "recommended_start": self.recommended_start,
            "recommended_end": self.recommended_end,
            "duration_min": self.duration_min,
            "deadline": self.deadline,
            "impact_score": self.impact_score,
            "confidence": self.confidence,
            "summary_text": self.summary_text,
            "impacted_apps": [
                {
                    "workload_uid": a.workload_uid,
                    "workload_kind": a.workload_kind,
                    "workload_name": a.workload_name,
                    "namespace": a.namespace,
                    "period_hours": a.period_hours,
                    "active_fraction": a.active_fraction,
                    "impact_score": a.impact_score,
                    "note": a.note,
                }
                for a in self.impacted_apps
            ],
            "evidence": [
                {
                    "workload_uid": e.workload_uid,
                    "resource": e.resource,
                    "forecast_series": e.forecast_series,
                    "active_windows": e.active_windows,
                }
                for e in self.evidence
            ],
        }
