"""Download EIA-930 half-year CSVs into immutable, dated raw snapshots.

Layout: data/raw/<snapshot_date>/<file>.csv plus manifest.json. Payloads that fail the
transport checks go to data/quarantine/<snapshot_date>/ with the reason in the manifest.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base
from urllib3.util.retry import Retry

from ts_tracker.config import Config
from ts_tracker.ingest.validate import Quality, validate_file, write_reports

# Header columns every file must carry; a missing one is a hard failure.
REQUIRED_COLUMNS = {
    "BALANCE": ["Balancing Authority", "UTC Time at End of Hour", "Region", "Demand (MW)"],
    "SUBREGION": ["Balancing Authority", "UTC Time at End of Hour", "Sub-Region", "Demand (MW)"],
}


@dataclass(frozen=True)
class Download:
    status: int
    content_type: str | None
    nbytes: int
    sha256: str
    head: bytes  # first bytes of the body, for the header check


# A fetcher streams the body of `url` to `dest` (only on HTTP 200) and describes what it got.
Fetcher = Callable[[str, Path], Download]


@dataclass(frozen=True)
class Source:
    kind: str  # BALANCE or SUBREGION
    period: str  # e.g. 2025_Jan_Jun

    @property
    def name(self) -> str:
        return f"EIA930_{self.kind}_{self.period}.csv"


def _session() -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,  # after the last retry, hand back the response instead of raising
    )
    session = requests.Session()
    session.headers["User-Agent"] = "ts-tracker"
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _download(session: requests.Session, url: str, dest: Path) -> Download:
    digest = hashlib.sha256()
    nbytes, head = 0, b""
    with session.get(url, stream=True, timeout=(10, 120)) as resp:
        if resp.status_code == 200:
            with dest.open("wb") as out:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    out.write(chunk)
                    digest.update(chunk)
                    head = head or chunk[:4096]
                    nbytes += len(chunk)
        return Download(
            resp.status_code, resp.headers.get("Content-Type"), nbytes, digest.hexdigest(), head
        )


def fetch(
    url: str,
    dest: Path,
    session: requests.Session | None = None,
    wait: wait_base | None = None,
) -> Download:
    """Stream a 200 response to `dest`, hashing as it arrives. Other statuses write nothing.

    The session's Retry covers HTTP statuses and connection setup. tenacity retries the whole
    download when the connection drops or times out mid-stream; each attempt starts `dest` over.
    """
    session = session or _session()
    retrying = Retrying(
        retry=retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)
        ),
        stop=stop_after_attempt(4),
        wait=wait or wait_exponential_jitter(initial=2, max=30),
        reraise=True,
    )
    return retrying(_download, session, url, dest)


def check_payload(source: Source, download: Download) -> str | None:
    """Transport and header checks. Return the failure reason, or None if the payload is fine."""
    if download.status != 200:
        return f"HTTP {download.status}"
    if not download.nbytes:
        return "empty body"
    if download.content_type and "html" in download.content_type.lower():
        return f"unexpected content type {download.content_type}"
    header = download.head.split(b"\n", 1)[0].decode("utf-8-sig", errors="replace")
    columns = [c.strip().strip('"') for c in header.split(",")]
    missing = [c for c in REQUIRED_COLUMNS[source.kind] if c not in columns]
    if missing:
        return f"missing columns {missing}"
    return None


def _write_json(path: Path, data: list | dict) -> None:
    """Write via a temp file so a crash never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def sources(cfg: Config) -> list[Source]:
    return [Source(kind, period) for period in cfg.ingest_periods for kind in cfg.ingest_kinds]


def snapshot_dir(cfg: Config, now: datetime) -> Path:
    return cfg.data_dir / "raw" / now.date().isoformat()


def latest_snapshot(cfg: Config, snapshot: str | None = None) -> Path:
    """The raw snapshot directory for a date, or the newest one when no date is given."""
    raw_root = cfg.data_dir / "raw"
    snapshots = sorted(p for p in raw_root.iterdir() if p.is_dir()) if raw_root.is_dir() else []
    if not snapshots:
        raise SystemExit("no raw snapshots found; run `just ingest` first")
    return raw_root / snapshot if snapshot else snapshots[-1]


def _save_quality(
    cfg: Config, raw_dir: Path, report: dict[str, dict], name: str, quality: Quality
) -> None:
    """Record one file's result: quality.json beside the manifest, Pointblank HTML in reports."""
    report[name] = quality.to_dict()
    _write_json(raw_dir / "quality.json", report)
    reports = cfg.reports_dir / "quality" / raw_dir.name
    reports.mkdir(parents=True, exist_ok=True)
    write_reports(quality, reports, Path(name).stem)


def ingest(cfg: Config, fetcher: Fetcher = fetch, now: datetime | None = None) -> list[dict]:
    """Fetch every configured source once per snapshot date; never overwrite a stored file.

    Transport failures and hard content failures quarantine the file. Soft content flags go to
    quality.json beside the manifest; the manifest itself stays transport-only.
    """
    now = now or datetime.now(UTC)
    raw_dir = snapshot_dir(cfg, now)
    quarantine_dir = cfg.data_dir / "quarantine" / now.date().isoformat()
    manifest_path = raw_dir / "manifest.json"
    quality_path = raw_dir / "quality.json"
    manifest: dict[str, dict] = (
        {e["file"]: e for e in json.loads(manifest_path.read_text())}
        if manifest_path.exists()
        else {}
    )
    report: dict[str, dict] = json.loads(quality_path.read_text()) if quality_path.exists() else {}

    raw_dir.mkdir(parents=True, exist_ok=True)
    for source in sources(cfg):
        if source.name in manifest and (raw_dir / source.name).exists():
            continue  # immutable: stored and recorded in this snapshot
        # A file with no manifest entry has unknown provenance, so it is fetched again.
        url = cfg.ingest_url_template.format(kind=source.kind, period=source.period)
        part = raw_dir / f"{source.name}.part"
        download = fetcher(url, part)
        reason = check_payload(source, download)
        quality = None
        if reason is None:
            quality = validate_file(source.kind, part, source.name)
            if quality.hard:
                reason = "content checks failed: " + "; ".join(quality.hard)
        if reason:
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            if part.exists():
                part.replace(quarantine_dir / source.name)
        else:
            part.replace(raw_dir / source.name)
        manifest[source.name] = {
            "file": source.name,
            "url": url,
            "status": download.status,
            "content_type": download.content_type,
            "bytes": download.nbytes,
            "sha256": download.sha256,
            "fetched_at": now.isoformat(),
            "quarantined": reason is not None,
            "reason": reason,
        }
        if quality:
            _save_quality(cfg, raw_dir, report, source.name, quality)
        _write_json(manifest_path, list(manifest.values()))  # per file, so a failure loses nothing

    _write_json(manifest_path, list(manifest.values()))
    return list(manifest.values())


def _quality_lines(report: dict[str, dict]) -> list[str]:
    lines = []
    for name, quality in report.items():
        lines += [f"  HARD {name}: {failure}" for failure in quality["hard"]]
        for check, failing in quality["soft"].items():
            lines.append(f"  flag {name}: {check}: {len(failing)} of {quality['series']} series")
    return lines


def main(cfg: Config) -> None:
    now = datetime.now(UTC)
    for entry in ingest(cfg, now=now):
        state = f"QUARANTINED ({entry['reason']})" if entry["quarantined"] else "ok"
        print(f"{entry['file']}: {entry['bytes']:,} bytes, {state}")
    quality_path = snapshot_dir(cfg, now) / "quality.json"
    if quality_path.exists():
        print("\n".join(_quality_lines(json.loads(quality_path.read_text()))))
        print(f"quality reports: {cfg.reports_dir / 'quality' / now.date().isoformat()}")


def validate_stored(cfg: Config, snapshot: str | None = None) -> Path:
    """Re-run the content checks on files already in a raw snapshot (default: the latest).

    Nothing is moved: a hard failure here is reported, and the file stays where it is.
    """
    raw_dir = latest_snapshot(cfg, snapshot)
    report: dict[str, dict] = {}
    for source in sources(cfg):
        path = raw_dir / source.name
        if path.exists():
            _save_quality(cfg, raw_dir, report, source.name, validate_file(source.kind, path))
    return raw_dir


def validate_main(cfg: Config, snapshot: str | None = None) -> None:
    raw_dir = validate_stored(cfg, snapshot)
    report = json.loads((raw_dir / "quality.json").read_text())
    print(f"validated {', '.join(report)} in {raw_dir}")
    print("\n".join(_quality_lines(report)))
    print(f"quality reports: {cfg.reports_dir / 'quality' / raw_dir.name}")
