"""Deterministic "why" summary templates. No LLM in v1."""
from __future__ import annotations


def summary(
    *,
    primary_resource: str,
    jump_pct: float,
    duration_str: str,
    cadence_str: str,
    other_resources: list[str],
    overlap_pct: float,
) -> str:
    """e.g. "CPU spikes ~300% for 25m every 6h; idle otherwise; memory aligns (overlap 96%)." """
    # Idle ≈ 0 makes the jump % unbounded; cap the display instead of printing ∞.
    jump_str = ">10000%" if (jump_pct == float("inf") or jump_pct > 10000) else f"~{round(jump_pct)}%"
    cadence = cadence_str if cadence_str.startswith(("every", "daily", "weekly")) else f"every {cadence_str}"
    text = f"{primary_resource.upper()} spikes {jump_str} for {duration_str} {cadence}; idle otherwise."
    if other_resources:
        names = " + ".join(other_resources)
        text += f" {names} align (overlap {round(overlap_pct)}%)."
    return text
