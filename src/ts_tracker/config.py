import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path("config.toml")


@dataclass(frozen=True)
class Config:
    bas: list[str]
    horizons: list[int]
    season_length: int
    cutoffs: list[str]
    data_dir: Path
    results_dir: Path


def load_config(path: Path = DEFAULT_CONFIG) -> Config:
    raw = tomllib.loads(path.read_text())
    return Config(
        bas=raw["scope"]["bas"],
        horizons=raw["forecast"]["horizons"],
        season_length=raw["forecast"]["season_length"],
        cutoffs=raw["forecast"]["cutoffs"],
        data_dir=Path(raw["paths"]["data_dir"]),
        results_dir=Path(raw["paths"]["results_dir"]),
    )
