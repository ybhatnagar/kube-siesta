"""EngineConfig — all algorithm thresholds are config-driven.

Defaults come from the state-DB `settings` row; per-run overrides arrive via the
`POST /runs` body / CLI flags. Cost-model node price/capacity are engine config
(a static on-prem fallback), not schema columns.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class EngineConfig:
    # --- scope / windowing ---
    resources: list[str] = field(default_factory=lambda: ["cpu", "memory"])
    window: str = "7d"
    resample_freq: str = "1h"
    min_period: int = 3            # hours; reject shorter periods
    min_periods_required: int = 2  # need >= this many full periods in the window

    # --- algorithm thresholds ---
    seasonality_strength_min: float = 0.30  # STL seasonal strength ("beats trend by 30%")
    band_pct: float = 0.10                  # active/idle band = median * (1 ± band_pct)
    jump_min: float = 50.0                  # peak-load-jump % floor per resource
    ratio_max: float = 0.5                  # drop workload if len(active)/len(idle) >= this
    union_max: float = 0.5                  # candidate only if active union < this frac of wall-clock
    overlap_min: float = 0.05               # inconclusive if cross-resource overlap < this
    period_tolerance: float = 0.25          # relative tolerance for resources agreeing on P
    peer_overlap_min: float = 0.5           # min active-window overlap to call a peer "aligned"
    offset_search: bool = True

    # --- cost model (static fallback; OpenCost later) ---
    node_cpu_m: float = 4000.0              # node capacity, millicores (4 vCPU)
    node_mem_bytes: float = 16 * 1024 ** 3  # node capacity, bytes (16 GiB)
    node_hourly_cost: float = 0.20          # $/hr
    hours_per_month: float = 730.0
    currency: str = "USD"

    # --- runtime ---
    concurrency: int = 1  # small fleets run sequentially

    def with_overrides(self, **kw: Any) -> "EngineConfig":
        """Return a copy with the given fields overridden (ignores None values)."""
        clean = {k: v for k, v in kw.items() if v is not None and k in self.__dataclass_fields__}
        return replace(self, **clean)

    @classmethod
    def from_settings(cls, settings: dict | None) -> "EngineConfig":
        """Build defaults from a state-DB `settings` row (thresholds JSON)."""
        if not settings:
            return cls()
        thr = settings.get("thresholds") or {}
        kw: dict[str, Any] = {}
        if "default_resources" in settings and settings["default_resources"]:
            kw["resources"] = [r.strip() for r in str(settings["default_resources"]).split(",") if r.strip()]
        if settings.get("default_window"):
            kw["window"] = settings["default_window"]
        mapping = {
            "seasonality_gain": "seasonality_strength_min",
            "band": "band_pct",
            "jump_min": "jump_min",
            "ratio_max": "ratio_max",
            "min_period": "min_period",
        }
        for src, dst in mapping.items():
            if src in thr and thr[src] is not None:
                kw[dst] = thr[src]
        return cls(**kw)
