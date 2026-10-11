import numpy as np

from ts_tracker.bench import run_bench
from ts_tracker.config import load_config
from ts_tracker.models.seasonal_naive import SeasonalNaive
from ts_tracker.sample import N_WEEKS, make_sample


def test_seasonal_naive_repeats_last_season():
    y = np.arange(8.0)
    pred = SeasonalNaive(season_length=4).fit(y).predict(6)
    assert pred.tolist() == [4, 5, 6, 7, 4, 5]


def test_sample_has_100_rows():
    assert make_sample().height == N_WEEKS == 100


def test_bench_returns_one_row_per_horizon():
    cfg = load_config()
    results = run_bench(make_sample(), cfg)
    assert results["horizon"].to_list() == cfg.horizons
    assert results["mase"].is_finite().all()
