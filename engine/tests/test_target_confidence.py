"""Stage 6 — target-heuristic branches, confidence levels, formatting."""
from engine.recommenders.job.target import choose_target, confidence, format_cadence, format_duration


def test_choose_target_branches():
    assert choose_target(baseline_ratio=0.0, num_active_windows=5, has_adhoc_interactions=True) == "Knative"
    assert choose_target(baseline_ratio=0.4, num_active_windows=5) == "KEDA"      # residual baseline
    assert choose_target(baseline_ratio=0.0, num_active_windows=1) == "Job"        # fires once
    assert choose_target(baseline_ratio=0.0, num_active_windows=10) == "CronJob"   # clean repeating burst


def test_confidence_levels():
    assert confidence(seasonal_strength=0.9, resources_agree=True, overlap_frac=1.0,
                      jump_pct=900, jump_min=50) == "high"
    assert confidence(seasonal_strength=0.35, resources_agree=False, overlap_frac=0.06,
                      jump_pct=60, jump_min=50) == "medium"
    assert confidence(seasonal_strength=0.0, resources_agree=False, overlap_frac=0.0,
                      jump_pct=0, jump_min=50) == "low"


def test_formatters():
    assert format_cadence(24) == "daily"
    assert format_cadence(168) == "weekly"
    assert format_cadence(8) == "every 8h"
    assert format_duration(120) == "2h"
    assert format_duration(45) == "45m"
