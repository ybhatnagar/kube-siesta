"""Stage 4 — Filters: peak-load-jump % and active/idle usage ratio."""
from __future__ import annotations

import numpy as np


def peak_load_jump_pct(values: np.ndarray, active: np.ndarray) -> float:
    """(mean_active − mean_idle) / mean_idle × 100. Large when idle ≈ 0 but active > 0."""
    values = np.asarray(values, dtype="float64")
    if not active.any() or active.all():
        return 0.0
    mean_active = float(values[active].mean())
    mean_idle = float(values[~active].mean())
    if mean_idle <= 0:
        return float("inf") if mean_active > 0 else 0.0
    return (mean_active - mean_idle) / mean_idle * 100.0


def usage_ratio(active: np.ndarray) -> float:
    """len(active) / len(idle); high means the workload is busy too often to shift."""
    n_active = int(active.sum())
    n_idle = int((~active).sum())
    if n_idle == 0:
        return float("inf")
    return n_active / n_idle


def baseline_ratio(values: np.ndarray, active: np.ndarray) -> float:
    """mean_idle / mean_active — residual baseline signal for the target heuristic."""
    values = np.asarray(values, dtype="float64")
    if not active.any() or active.all():
        return 0.0
    mean_active = float(values[active].mean())
    mean_idle = float(values[~active].mean())
    if mean_active <= 0:
        return 0.0
    return max(0.0, mean_idle / mean_active)
