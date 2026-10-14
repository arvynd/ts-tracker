import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests
from tenacity import wait_none

from ts_tracker.config import Config
from ts_tracker.ingest.eia import Download, fetch, ingest

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
