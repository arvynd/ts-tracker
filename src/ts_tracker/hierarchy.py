"""Leaf -> BA -> region -> total map for the series in scope.

One row per leaf. Aggregating the leaves and building the summing matrix happen later, in data prep.
"""

from pathlib import Path

import polars as pl

from ts_tracker.config import Config
from ts_tracker.ingest.eia import latest_snapshot, sources

TOTAL = "TOTAL"
MAP_COLUMNS = ["leaf", "ba", "region", "total"]
BA = "Balancing Authority"


def read_inputs(cfg: Config, raw_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Unique (BA, Region) pairs from BALANCE and unique (BA, Sub-Region) pairs from SUBREGION.

    Pairs are collected over every stored period. BALANCE lists every BA; SUBREGION only the BAs
    that report sub-regions.
    """
    column = {"BALANCE": "Region", "SUBREGION": "Sub-Region"}
    frames: dict[str, list[pl.DataFrame]] = {kind: [] for kind in column}
    for source in sources(cfg):
        path = raw_dir / source.name
        if path.exists():
            frames[source.kind].append(
                pl.read_csv(path, infer_schema_length=0, columns=[BA, column[source.kind]])
            )
    if not frames["BALANCE"]:
        raise FileNotFoundError(f"no BALANCE file in {raw_dir}")
    empty = pl.DataFrame(schema={BA: pl.String, "Sub-Region": pl.String})
    balance = pl.concat(frames["BALANCE"]).unique()
    subregion = pl.concat(frames["SUBREGION"]).unique() if frames["SUBREGION"] else empty
    return balance, subregion


def leaf_map(balance: pl.DataFrame, subregion: pl.DataFrame, scope: list[str]) -> pl.DataFrame:
    """One row per leaf in scope, with the columns in MAP_COLUMNS.

    balance:   (Balancing Authority, Region), every BA.
    subregion: (Balancing Authority, Sub-Region), only BAs that report them.
    Hierarchy is highest to lowest ALL -> Region -> BA -> Sub-Regions
    A BA with no sub-regions is its own leaf.
    """
    hierarchy = balance.join(
        subregion,
        how="full",
        on="Balancing Authority",
        coalesce=True,
    ).rename({BA: "ba", "Region": "region", "Sub-Region": "leaf"})
    return hierarchy


def build(cfg: Config, snapshot: str | None = None) -> Path:
    """Write leaf_map.parquet for a raw snapshot (default: the latest)."""
    raw_dir = latest_snapshot(cfg, snapshot)
    balance, subregion = read_inputs(cfg, raw_dir)
    out = cfg.data_dir / "hierarchy" / raw_dir.name
    out.mkdir(parents=True, exist_ok=True)
    leaf_map(balance, subregion, cfg.bas).write_parquet(out / "leaf_map.parquet")
    return out


def main(cfg: Config, snapshot: str | None = None) -> None:
    out = build(cfg, snapshot)
    leaves = pl.read_parquet(out / "leaf_map.parquet")
    print(
        f"{leaves.height} leaves, {leaves['ba'].n_unique()} BAs, "
        f"{leaves['region'].n_unique()} regions -> {out}"
    )
