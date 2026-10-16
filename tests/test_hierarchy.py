"""What the leaf map must satisfy. The leaf_map tests fail until leaf_map is written."""

import polars as pl

from ts_tracker.config import Config
from ts_tracker.hierarchy import MAP_COLUMNS, TOTAL, leaf_map, read_inputs

BA = "Balancing Authority"


def balance(*pairs: tuple[str, str]) -> pl.DataFrame:
    return pl.DataFrame(pairs, schema=[BA, "Region"], orient="row")


def subregion(*pairs: tuple[str, str]) -> pl.DataFrame:
    return pl.DataFrame(pairs, schema=[BA, "Sub-Region"], orient="row")


def rows(frame: pl.DataFrame) -> list[tuple]:
    return frame.sort("leaf").select(MAP_COLUMNS).rows()


def test_subregions_become_leaves():
    out = leaf_map(
        balance(("A", "R1"), ("B", "R2")),
        subregion(("A", "a1"), ("A", "a2"), ("B", "b1")),
        ["A", "B"],
    )
    assert rows(out) == [
        ("A/a1", "A", "R1", TOTAL),
        ("A/a2", "A", "R1", TOTAL),
        ("B/b1", "B", "R2", TOTAL),
    ]


def test_ba_without_subregions_is_its_own_leaf():
    out = leaf_map(balance(("A", "R1"), ("D", "R2")), subregion(("A", "a1")), ["A", "D"])
    assert rows(out) == [("A/a1", "A", "R1", TOTAL), ("D", "D", "R2", TOTAL)]


def test_only_bas_in_scope_are_mapped():
    out = leaf_map(balance(("A", "R1"), ("X", "R9")), subregion(("A", "a1"), ("X", "x1")), ["A"])
    assert set(out["ba"]) == {"A"}


def test_leaves_are_unique_and_each_ba_has_one_region():
    out = leaf_map(
        balance(("A", "R1"), ("B", "R1")),
        subregion(("A", "a1"), ("A", "a2"), ("B", "b1")),
        ["A", "B"],
    )
    assert out["leaf"].is_unique().all()
    assert (
        out.group_by("ba").agg(pl.col("region").n_unique()).filter(pl.col("region") > 1).is_empty()
    )


def test_read_inputs_collects_unique_pairs_from_the_stored_files(tmp_path):
    cfg = Config(
        bas=["A"],
        horizons=[1],
        season_length=52,
        cutoffs=[],
        ingest_url_template="https://example.test/{kind}_{period}.csv",
        ingest_kinds=["BALANCE", "SUBREGION"],
        ingest_periods=["p1"],
        data_dir=tmp_path / "data",
        results_dir=tmp_path / "results",
        reports_dir=tmp_path / "reports",
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "EIA930_BALANCE_p1.csv").write_text(f"{BA},Region,Demand (MW)\nA,R1,1\nA,R1,2\nB,R2,3\n")
    (raw / "EIA930_SUBREGION_p1.csv").write_text(f"{BA},Sub-Region,Demand (MW)\nA,a1,1\nA,a1,2\n")
    bal, sub = read_inputs(cfg, raw)
    assert sorted(bal.rows()) == [("A", "R1"), ("B", "R2")]
    assert sub.rows() == [("A", "a1")]
