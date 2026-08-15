"""Stage 6b — Tiered target heuristic: Job / CronJob / KEDA / Knative."""
from __future__ import annotations

from typing import Optional


def choose_target(
    *,
    baseline_ratio: float,
    num_active_windows: int,
    has_adhoc_interactions: bool = False,
    residual_baseline_max: float = 0.25,
) -> str:
    """Pick the migration target.

    - Unpredictable short request-driven invocations → **Knative**.
    - Residual low baseline / sporadic ad-hoc traffic in idle windows → **KEDA**
      (scale-to-zero, wakes on demand).
    - Clean single scheduled burst, no residual baseline → **CronJob**
      (**Job** if it fires only once in the window).
    """
    if has_adhoc_interactions:
        return "Knative"
    if baseline_ratio > residual_baseline_max:
        return "KEDA"
    if num_active_windows <= 1:
        return "Job"
    return "CronJob"


def confidence(
    *,
    seasonal_strength: float,
    resources_agree: bool,
    overlap_frac: float,
    jump_pct: float,
    jump_min: float,
) -> str:
    """Combine seasonal strength, resource agreement, overlap and jump margin."""
    score = 0.0
    score += 1.0 if seasonal_strength >= 0.6 else (0.5 if seasonal_strength >= 0.30 else 0.0)
    score += 1.0 if resources_agree else 0.0
    score += 1.0 if overlap_frac >= 0.5 else (0.5 if overlap_frac >= 0.05 else 0.0)
    score += 1.0 if jump_pct >= 3 * jump_min else (0.5 if jump_pct >= jump_min else 0.0)
    if score >= 3.0:
        return "high"
    if score >= 1.5:
        return "medium"
    return "low"


def format_cadence(period_hours: float) -> str:
    if abs(period_hours - 24) <= 2:
        return "daily"
    if abs(period_hours - 168) <= 6:
        return "weekly"
    if period_hours < 1:                      # sub-hour periods -> minutes
        return f"every {max(1, round(period_hours * 60))}m"
    if abs(period_hours - 12) <= 1:
        return "every 12h"
    return f"every {round(period_hours)}h"


def format_duration(active_minutes: float) -> str:
    m = int(round(active_minutes))
    if m >= 120 and m % 60 == 0:
        return f"{m // 60}h"
    if m >= 90:
        h, rem = divmod(m, 60)
        return f"{h}h{rem}m" if rem else f"{h}h"
    return f"{m}m"


def format_run_time(start: Optional[object]) -> str:
    """Format the active-window start as HH:MM UTC (start is a datetime or None)."""
    if start is None:
        return "n/a"
    return start.strftime("%H:%M UTC")
