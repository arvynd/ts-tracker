import numpy as np


class SeasonalNaive:
    """Repeat the last observed season forward."""

    def __init__(self, season_length: int):
        self.season_length = season_length
        self._last_season: np.ndarray | None = None

    def fit(self, y: np.ndarray) -> "SeasonalNaive":
        if len(y) < self.season_length:
            raise ValueError("need at least one full season of history")
        self._last_season = np.asarray(y[-self.season_length :], dtype=float)
        return self

    def predict(self, horizon: int) -> np.ndarray:
        if self._last_season is None:
            raise RuntimeError("call fit before predict")
        reps = -(-horizon // self.season_length)
        return np.tile(self._last_season, reps)[:horizon]
