from datetime import datetime, timedelta
from pathlib import Path

from ts_tracker.ingest.validate import validate_file

START = datetime(2025, 1, 1, 1)


def stamp(hour: int) -> str:
    return (START + timedelta(hours=hour)).strftime("%m/%d/%Y %I:%M:%S %p")


def balance_csv(tmp_path: Path, rows: list[tuple]) -> Path:
    """rows are (ba, hour offset or raw timestamp, region, demand)."""
    lines = ["Balancing Authority,UTC Time at End of Hour,Region,Demand (MW)"]
    for ba, hour, region, demand in rows:
        when = stamp(hour) if isinstance(hour, int) else hour
        lines.append(f"{ba},{when},{region},{demand}")
    path = tmp_path / "balance.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


def subregion_csv(tmp_path: Path, rows: list[tuple]) -> Path:
    lines = ["Balancing Authority,UTC Time at End of Hour,Sub-Region,Demand (MW)"]
    lines += [f"{ba},{stamp(hour)},{sub},{demand}" for ba, hour, sub, demand in rows]
    path = tmp_path / "subregion.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


def hourly(ba: str, n: int, demand="100", region="MIDA") -> list[tuple]:
    return [(ba, h, region, demand) for h in range(n)]


def test_clean_file_has_no_failures_or_flags(tmp_path):
    q = validate_file("BALANCE", balance_csv(tmp_path, hourly("PJM", 5)))
    assert (q.rows, q.series, q.hard, q.soft) == (5, 1, [], {})


def test_duplicate_key_is_a_hard_failure(tmp_path):
    rows = hourly("PJM", 3) + [("PJM", 1, "MIDA", "100")]
    q = validate_file("BALANCE", balance_csv(tmp_path, rows))
    assert q.hard == ["duplicate keys (2 rows)"]


def test_unparsable_timestamp_is_a_hard_failure(tmp_path):
    rows = hourly("PJM", 3) + [("PJM", "not a time", "MIDA", "100")]
    q = validate_file("BALANCE", balance_csv(tmp_path, rows))
    assert q.hard == ["nulls in ts (1 rows)"]


def test_missing_region_is_a_hard_failure_for_balance(tmp_path):
    q = validate_file("BALANCE", balance_csv(tmp_path, hourly("PJM", 3, region="")))
    assert q.hard == ["nulls in Region (3 rows)"]


def test_header_only_file_has_no_rows(tmp_path):
    q = validate_file("BALANCE", balance_csv(tmp_path, []))
    assert q.hard == ["no data rows"]


def test_soft_flags_name_the_failing_series_and_leave_others_alone(tmp_path):
    rows = (
        hourly("PJM", 200)
        + hourly("NEG", 200)[:-1]
        + [("NEG", 199, "MIDA", "-5")]
        + [("ZERO", h, "MIDA", "0" if h == 0 else "100") for h in range(200)]
        + hourly("NULLS", 200, demand="")
        + [("GAP", h, "MIDA", "100") for h in range(200) if h not in (50, 51)]
    )
    q = validate_file("BALANCE", balance_csv(tmp_path, rows))
    assert q.hard == []

    def flagged(check):
        return [row["Balancing Authority"] for row in q.soft[check]]

    assert flagged("negative demand") == ["NEG"]
    assert flagged("zero demand") == ["ZERO"]
    assert flagged("reports no demand") == ["NULLS"]
    assert flagged("null share above 1%") == ["NULLS"]
    assert flagged("gaps inside the series' own span") == ["GAP"]
    assert q.soft["gaps inside the series' own span"][0]["interior_gaps"] == 2


def test_series_starting_later_than_others_is_not_a_gap(tmp_path):
    rows = hourly("EARLY", 100) + [("LATE", h, "MIDA", "100") for h in range(40, 100)]
    q = validate_file("BALANCE", balance_csv(tmp_path, rows))
    assert q.soft == {}


def test_subregion_series_are_keyed_by_ba_and_sub_region(tmp_path):
    rows = [("PJM", h, sub, "100") for sub in ("AE", "PS") for h in range(5)]
    rows += [("PJM", h, "DOM", "0") for h in range(5)]
    q = validate_file("SUBREGION", subregion_csv(tmp_path, rows))
    assert q.series == 3
    assert q.soft["zero demand"] == [
        {"Balancing Authority": "PJM", "Sub-Region": "DOM", "zeros": 5}
    ]
