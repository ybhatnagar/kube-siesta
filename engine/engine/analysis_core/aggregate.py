"""Stage 5 — Cross-resource aggregation: union / overlap of active intervals."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Aggregate:
    union: np.ndarray          # OR of active masks
    intersection: np.ndarray   # AND of active masks
    union_frac: float          # active union as fraction of wall-clock
    overlap_frac: float        # |intersection| / |union|
    windows: list[tuple]       # consolidated active runs on the union mask


def aggregate(active_by_resource: dict[str, np.ndarray]) -> Aggregate:
    masks = list(active_by_resource.values())
    n = len(masks[0])
    union = np.zeros(n, dtype=bool)
    intersection = np.ones(n, dtype=bool)
    for m in masks:
        union |= m
        intersection &= m

    n_union = int(union.sum())
    union_frac = n_union / n if n else 0.0
    overlap_frac = (int(intersection.sum()) / n_union) if n_union else 0.0

    return Aggregate(
        union=union,
        intersection=intersection,
        union_frac=union_frac,
        overlap_frac=overlap_frac,
        windows=_contiguous_true(union),
    )


def _contiguous_true(mask: np.ndarray) -> list[tuple]:
    runs = []
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs
