from datetime import date, timedelta

import numpy as np
import polars as pl

N_WEEKS = 100


def make_sample(seed: int = 0) -> pl.DataFrame:
    """Synthetic weekly series (100 rows) with yearly seasonality, for smoke tests."""
    rng = np.random.default_rng(seed)
    t = np.arange(N_WEEKS)
    y = 100 + 20 * np.sin(2 * np.pi * t / 52) + rng.normal(0, 3, N_WEEKS)
    start = date(2024, 1, 1)
    return pl.DataFrame(
        {
            "unique_id": ["sample"] * N_WEEKS,
            "ds": [start + timedelta(weeks=int(i)) for i in t],
            "y": y,
        }
    )
