import numpy as np
import polars as pl

from ts_tracker.config import Config
from ts_tracker.models.seasonal_naive import SeasonalNaive


def mase(actual: np.ndarray, pred: np.ndarray, train: np.ndarray, season_length: int) -> float:
    scale = np.mean(np.abs(train[season_length:] - train[:-season_length]))
    return float(np.mean(np.abs(actual - pred)) / scale)


def run_bench(df: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    """Seasonal-naive backtest from one cutoff: hold out the longest horizon."""
    rows = []
    holdout = max(cfg.horizons)
    for uid, g in df.sort("ds").group_by("unique_id", maintain_order=True):
        y = g["y"].to_numpy()
        train, test = y[:-holdout], y[-holdout:]
        model = SeasonalNaive(cfg.season_length).fit(train)
        pred = model.predict(holdout)
        for h in cfg.horizons:
            rows.append(
                {
                    "unique_id": uid[0],
                    "model": "seasonal_naive",
                    "horizon": h,
                    "mase": mase(test[:h], pred[:h], train, cfg.season_length),
                }
            )
    return pl.DataFrame(rows)
