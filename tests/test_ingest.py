import dataclasses
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests
from tenacity import wait_none

from ts_tracker.config import Config
from ts_tracker.ingest.eia import Download, fetch, ingest, validate_stored

BALANCE_CSV = (
    b"Balancing Authority,UTC Time at End of Hour,Region,Demand (MW)\n"
    b"PJM,01/01/2025 01:00:00 AM,Mid-Atlantic,90000\n"
)
NOW = datetime(2025, 7, 15, 12, 0, tzinfo=UTC)


def make_cfg(tmp_path: Path) -> Config:
    return Config(
        bas=["PJM"],
        horizons=[1],
        season_length=52,
        cutoffs=[],
        ingest_url_template="https://example.test/{kind}_{period}.csv",
        ingest_kinds=["BALANCE"],
        ingest_periods=["2025_Jan_Jun"],
        data_dir=tmp_path / "data",
        results_dir=tmp_path / "results",
        reports_dir=tmp_path / "reports",
    )


def fake(status=200, content_type="text/csv", body=BALANCE_CSV):
    calls = []

    def fetcher(url, dest):
        calls.append(url)
        if status == 200:
            dest.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        return Download(status, content_type, len(body), digest, body[:4096])

    fetcher.calls = calls
    return fetcher


def test_good_payload_is_stored_with_manifest(tmp_path):
    cfg = make_cfg(tmp_path)
    (entry,) = ingest(cfg, fake(), NOW)
    stored = cfg.data_dir / "raw" / "2025-07-15" / "EIA930_BALANCE_2025_Jan_Jun.csv"
    assert stored.read_bytes() == BALANCE_CSV
    assert not entry["quarantined"] and entry["bytes"] == len(BALANCE_CSV)
    manifest = json.loads((stored.parent / "manifest.json").read_text())
    assert manifest[0]["sha256"] == entry["sha256"]


def test_same_snapshot_is_not_overwritten_or_refetched(tmp_path):
    cfg = make_cfg(tmp_path)
    ingest(cfg, fake(), NOW)
    again = fake(body=BALANCE_CSV + b"changed\n")
    ingest(cfg, again, NOW)
    assert again.calls == []
    stored = cfg.data_dir / "raw" / "2025-07-15" / "EIA930_BALANCE_2025_Jan_Jun.csv"
    assert stored.read_bytes() == BALANCE_CSV


def test_stored_file_without_manifest_entry_is_fetched_again(tmp_path):
    cfg = make_cfg(tmp_path)
    ingest(cfg, fake(), NOW)
    raw_dir = cfg.data_dir / "raw" / "2025-07-15"
    (raw_dir / "manifest.json").unlink()
    fetcher = fake()
    (entry,) = ingest(cfg, fetcher, NOW)
    assert len(fetcher.calls) == 1
    assert entry["sha256"] == hashlib.sha256(BALANCE_CSV).hexdigest()
    assert json.loads((raw_dir / "manifest.json").read_text())[0]["file"] == entry["file"]


def test_hard_content_failure_quarantines_the_file(tmp_path):
    duplicated = BALANCE_CSV + BALANCE_CSV.split(b"\n", 1)[1]
    cfg = make_cfg(tmp_path)
    (entry,) = ingest(cfg, fake(body=duplicated), NOW)
    assert entry["quarantined"]
    assert entry["reason"] == "content checks failed: duplicate keys (2 rows)"
    assert not (cfg.data_dir / "raw" / "2025-07-15" / entry["file"]).exists()
    assert (cfg.data_dir / "quarantine" / "2025-07-15" / entry["file"]).exists()


def test_soft_flags_go_to_quality_json_and_the_file_is_kept(tmp_path):
    zero = BALANCE_CSV.replace(b",90000", b",0")
    cfg = make_cfg(tmp_path)
    (entry,) = ingest(cfg, fake(body=zero), NOW)
    raw_dir = cfg.data_dir / "raw" / "2025-07-15"
    assert not entry["quarantined"] and (raw_dir / entry["file"]).exists()
    quality = json.loads((raw_dir / "quality.json").read_text())[entry["file"]]
    assert quality["hard"] == [] and "zero demand" in quality["soft"]
    assert "soft" not in entry and "zero demand" not in json.dumps(entry)


def test_ingest_writes_pointblank_html_reports(tmp_path):
    cfg = make_cfg(tmp_path)
    ingest(cfg, fake(), NOW)
    out = cfg.reports_dir / "quality" / "2025-07-15"
    assert sorted(p.name for p in out.iterdir()) == [
        "EIA930_BALANCE_2025_Jan_Jun_hard.html",
        "EIA930_BALANCE_2025_Jan_Jun_soft.html",
    ]
    assert "<table" in (out / "EIA930_BALANCE_2025_Jan_Jun_soft.html").read_text()


def test_hard_failure_still_gets_a_hard_report_and_no_soft_one(tmp_path):
    duplicated = BALANCE_CSV + BALANCE_CSV.split(b"\n", 1)[1]
    cfg = make_cfg(tmp_path)
    ingest(cfg, fake(body=duplicated), NOW)
    out = cfg.reports_dir / "quality" / "2025-07-15"
    assert [p.name for p in out.iterdir()] == ["EIA930_BALANCE_2025_Jan_Jun_hard.html"]


def test_validate_stored_checks_files_already_in_raw(tmp_path):
    cfg = make_cfg(tmp_path)
    ingest(cfg, fake(), NOW)
    raw_dir = cfg.data_dir / "raw" / "2025-07-15"
    (raw_dir / "quality.json").unlink()
    for html in (cfg.reports_dir / "quality" / "2025-07-15").iterdir():
        html.unlink()
    assert validate_stored(cfg) == raw_dir
    assert json.loads((raw_dir / "quality.json").read_text())["EIA930_BALANCE_2025_Jan_Jun.csv"]
    assert len(list((cfg.reports_dir / "quality" / "2025-07-15").iterdir())) == 2


def test_bad_payloads_are_quarantined(tmp_path):
    bad = {
        "html": fake(content_type="text/html", body=b"<html>503</html>"),
        "empty": fake(body=b""),
        "http error": fake(status=503, body=b""),
        "renamed column": fake(body=BALANCE_CSV.replace(b"Demand (MW)", b"Load (MW)")),
    }
    for name, fetcher in bad.items():
        cfg = make_cfg(tmp_path / name.replace(" ", "_"))
        (entry,) = ingest(cfg, fetcher, NOW)
        assert entry["quarantined"], name
        raw = cfg.data_dir / "raw" / "2025-07-15" / "EIA930_BALANCE_2025_Jan_Jun.csv"
        assert not raw.exists(), name


class FakeResponse:
    status_code = 200
    headers = {"Content-Type": "text/csv"}

    def __init__(self, chunks, fail_after=None):
        self.chunks, self.fail_after = chunks, fail_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        for i, chunk in enumerate(self.chunks):
            if self.fail_after is not None and i == self.fail_after:
                raise requests.exceptions.ChunkedEncodingError("connection dropped")
            yield chunk


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_fetch_retries_a_mid_stream_drop_and_restarts_the_file(tmp_path):
    chunks = [b"Balancing Authority,", b"rest\n"]
    session = FakeSession([FakeResponse(chunks, fail_after=1), FakeResponse(chunks)])
    dest = tmp_path / "f.csv"
    result = fetch("https://example.test/f.csv", dest, session=session, wait=wait_none())
    assert session.calls == 2
    assert dest.read_bytes() == b"".join(chunks)
    assert result.nbytes == len(b"".join(chunks))


def test_fetch_gives_up_after_four_attempts(tmp_path):
    chunks = [b"a", b"b"]
    session = FakeSession([FakeResponse(chunks, fail_after=1) for _ in range(4)])
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        fetch("https://example.test/f.csv", tmp_path / "f.csv", session=session, wait=wait_none())
    assert session.calls == 4


def test_manifest_keeps_finished_files_when_a_later_fetch_raises(tmp_path):
    cfg = dataclasses.replace(make_cfg(tmp_path), ingest_kinds=["BALANCE", "SUBREGION"])
    good = fake()

    def flaky(url, dest):
        if "SUBREGION" in url:
            raise requests.ConnectionError("down")
        return good(url, dest)

    with pytest.raises(requests.ConnectionError):
        ingest(cfg, flaky, NOW)
    manifest = json.loads((cfg.data_dir / "raw" / "2025-07-15" / "manifest.json").read_text())
    assert [e["file"] for e in manifest] == ["EIA930_BALANCE_2025_Jan_Jun.csv"]
