"""Content checks on a downloaded EIA-930 file, run once at ingest.

Hard checks run on the rows; any failure means the file is quarantined. Soft checks run on a
one-row-per-series summary, so each failing row is a series; they are reported and the data is
kept. Coverage is judged against each series' own span, because series start and stop at
different UTC hours.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pointblank as pb
import polars as pl

TIME_FORMAT = "%m/%d/%Y %I:%M:%S %p"
NULL_SHARE_LIMIT = 0.01

KEYS = {
    "BALANCE": ["Balancing Authority"],
    "SUBREGION": ["Balancing Authority", "Sub-Region"],
}

# (check name, summary column it reads), in the order they are run.
SOFT_CHECKS = [
    ("null share above 1%", "null_share"),
    ("reports no demand", "all_null"),
    ("negative demand", "negatives"),
    ("zero demand", "zeros"),
    ("gaps inside the series' own span", "interior_gaps"),
]


@dataclass(frozen=True)
class Quality:
    rows: int
    series: int
    hard: list[str] = field(default_factory=list)  # any entry means quarantine
    soft: dict[str, list[dict]] = field(default_factory=dict)  # check -> failing series
    # Pointblank's own results, kept for its HTML report; soft is None when it was not run.
    hard_validation: pb.Validate | None = field(default=None, compare=False, repr=False)
    soft_validation: pb.Validate | None = field(default=None, compare=False, repr=False)

    def to_dict(self) -> dict:
        return {"rows": self.rows, "series": self.series, "hard": self.hard, "soft": self.soft}


def _load(kind: str, path: Path) -> pl.DataFrame:
    keys = KEYS[kind]
    extra = ["Region"] if kind == "BALANCE" else []
    raw = pl.read_csv(
        path,
        infer_schema_length=0,
        columns=[*keys, *extra, "UTC Time at End of Hour", "Demand (MW)"],
    )
    utc = pl.col("UTC Time at End of Hour")
    demand = pl.col("Demand (MW)")
    return raw.select(
        *keys,
        *extra,
        utc.str.strptime(pl.Datetime, TIME_FORMAT, strict=False).alias("ts"),
        demand.str.replace_all(",", "").cast(pl.Float64, strict=False).alias("demand"),
    )


def _hard_checks(df: pl.DataFrame, kind: str, name: str) -> pb.Validate:
    keys = KEYS[kind]
    required = [*keys, "ts", *(["Region"] if kind == "BALANCE" else [])]
    validation = pb.Validate(
        df,
        tbl_name=name,
        label="Hard checks (failure quarantines the file)",
        thresholds=pb.Thresholds(critical=1),
    )
    for column in required:
        validation = validation.col_vals_not_null(column, brief=f"nulls in {column}")
    validation = validation.rows_distinct(columns_subset=[*keys, "ts"], brief="duplicate keys")
    return validation.interrogate()


def _series_summary(df: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    return (
        df.group_by(keys)
        .agg(
            pl.len().alias("rows"),
            pl.col("ts").min().alias("first"),
            pl.col("ts").max().alias("last"),
            pl.col("demand").is_null().sum().alias("nulls"),
            (pl.col("demand") == 0).sum().alias("zeros"),
            (pl.col("demand") < 0).sum().alias("negatives"),
        )
        .with_columns(
            (pl.col("nulls") / pl.col("rows")).alias("null_share"),
            (pl.col("nulls") == pl.col("rows")).cast(pl.Int8).alias("all_null"),
            ((pl.col("last") - pl.col("first")).dt.total_hours() + 1 - pl.col("rows")).alias(
                "interior_gaps"
            ),
        )
        .sort(keys)
    )


def _soft_checks(summary: pl.DataFrame, name: str) -> pb.Validate:
    return (
        pb.Validate(
            summary,
            tbl_name=f"{name}, one row per series",
            label="Soft checks (a failing row is a flagged series)",
            thresholds=pb.Thresholds(warning=1),
        )
        .col_vals_le("null_share", NULL_SHARE_LIMIT, brief=SOFT_CHECKS[0][0])
        .col_vals_eq("all_null", 0, brief=SOFT_CHECKS[1][0])
        .col_vals_eq("negatives", 0, brief=SOFT_CHECKS[2][0])
        .col_vals_eq("zeros", 0, brief=SOFT_CHECKS[3][0])
        .col_vals_eq("interior_gaps", 0, brief=SOFT_CHECKS[4][0])
        .interrogate()
    )


def _failing_series(validation: pb.Validate, keys: list[str]) -> dict[str, list[dict]]:
    columns = dict(SOFT_CHECKS)
    flags = {}
    for info in validation.validation_info:
        if info.all_passed:
            continue
        failing = validation.get_data_extracts(i=info.i, frame=True)
        flags[info.brief] = failing.select([*keys, columns[info.brief]]).to_dicts()
    return flags


def validate_file(kind: str, path: Path, name: str | None = None) -> Quality:
    """Hard failures first; soft checks only run once the rows are structurally sound."""
    name = name or path.name
    df = _load(kind, path)
    if df.height == 0:
        return Quality(rows=0, series=0, hard=["no data rows"])
    hard_validation = _hard_checks(df, kind, name)
    hard = [
        f"{info.brief} ({info.n_failed:,} rows)"
        for info in hard_validation.validation_info
        if info.critical
    ]
    summary = _series_summary(df, KEYS[kind])
    if hard:
        return Quality(df.height, summary.height, hard=hard, hard_validation=hard_validation)
    soft_validation = _soft_checks(summary, name)
    return Quality(
        df.height,
        summary.height,
        soft=_failing_series(soft_validation, KEYS[kind]),
        hard_validation=hard_validation,
        soft_validation=soft_validation,
    )


def write_reports(quality: Quality, directory: Path, stem: str) -> list[Path]:
    """Write Pointblank's HTML report for each check set that ran."""
    written = []
    for suffix, validation in (
        ("hard", quality.hard_validation),
        ("soft", quality.soft_validation),
    ):
        if validation is not None:
            path = directory / f"{stem}_{suffix}.html"
            path.write_text(validation.get_tabular_report().as_raw_html())
            written.append(path)
    return written
