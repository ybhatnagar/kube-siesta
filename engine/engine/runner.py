"""Runner dispatch: pick the right recommender head by `run_type`.

Today only the job head is wired; the maintenance head lands in a later
milestone (docs/07). Keeping `run_analysis` as the shared public entrypoint
(imported by the API and the CLI) means callers don't need to know which head
they're on.
"""
from __future__ import annotations

from typing import Any, Optional

from .analysis_core.io.statestore import StateStore
from .recommenders.job.runner import RunResult as JobRunResult
from .recommenders.job.runner import run_job_analysis
from .recommenders.maintenance.runner import RunResult as MaintenanceRunResult
from .recommenders.maintenance.runner import run_maintenance_analysis


def run_analysis(
    store: StateStore,
    *,
    cluster: Any,
    scope: Any = "all",
    config_overrides: Optional[dict] = None,
    ttl_hours: int = 24,
    collect_data: bool = False,
    name: Optional[str] = None,
    run_type: str = "job",
    **kwargs,
):
    """Dispatch to the recommender head for `run_type` (default: job).

    Job kwargs: none beyond the shared ones.
    Maintenance kwargs (required): target_workload_uid, duration, deadline.
      Optional: now (datetime, for deterministic tests).
    """
    if run_type == "job":
        return run_job_analysis(
            store,
            cluster=cluster, scope=scope, config_overrides=config_overrides,
            ttl_hours=ttl_hours, collect_data=collect_data, name=name, **kwargs,
        )
    if run_type == "maintenance":
        # collect_data isn't yet wired for maintenance; docs/07 treats collection
        # as a shared upstream concern and the API surface lands in M4.
        if collect_data:
            raise NotImplementedError("collect_data is not yet supported for maintenance runs")
        return run_maintenance_analysis(
            store,
            cluster=cluster, scope=scope, config_overrides=config_overrides,
            ttl_hours=ttl_hours, name=name, **kwargs,
        )
    raise ValueError(f"unknown run_type: {run_type!r}")


__all__ = ["run_analysis", "JobRunResult", "MaintenanceRunResult"]
