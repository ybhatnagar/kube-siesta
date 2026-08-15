"""Stage 0 — resample to hourly, gap fill, coverage check."""
from datetime import datetime, timedelta, timezone

from engine.analysis_core.prepare import has_min_coverage, prepare_series


def test_resample_and_fill_small_gap():
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    # missing hour 2 — should be interpolated
    pts = [(base + timedelta(hours=h), float(h)) for h in (0, 1, 3, 4)]
    s = prepare_series(pts, "1h")
    assert len(s) == 5           # buckets 0..4 after resample
    assert not s.isna().any()    # gap filled


def test_prepare_empty():
    assert prepare_series([], "1h").empty


def test_has_min_coverage():
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    s = prepare_series([(base + timedelta(hours=h), 1.0) for h in range(20)], "1h")
    assert has_min_coverage(s, period_hours=8, min_periods=2)       # 20 >= 16
    assert not has_min_coverage(s, period_hours=8, min_periods=3)   # 20 < 24
