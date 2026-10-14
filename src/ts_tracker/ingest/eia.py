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


def _write_manifest(path: Path, manifest: dict[str, dict]) -> None:
    """Write via a temp file so a crash never leaves a half-written manifest."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(list(manifest.values()), indent=2) + "\n")
    tmp.replace(path)


def sources(cfg: Config) -> list[Source]:
    return [Source(kind, period) for period in cfg.ingest_periods for kind in cfg.ingest_kinds]


def ingest(cfg: Config, fetcher: Fetcher = fetch, now: datetime | None = None) -> list[dict]:
    """Fetch every configured source once per snapshot date; never overwrite a stored file."""
    now = now or datetime.now(UTC)
    snapshot = now.date().isoformat()
    raw_dir = cfg.data_dir / "raw" / snapshot
    quarantine_dir = cfg.data_dir / "quarantine" / snapshot
    manifest_path = raw_dir / "manifest.json"
    manifest: dict[str, dict] = (
        {e["file"]: e for e in json.loads(manifest_path.read_text())}
        if manifest_path.exists()
        else {}
    )

    raw_dir.mkdir(parents=True, exist_ok=True)
    for source in sources(cfg):
        if (raw_dir / source.name).exists():
            continue  # immutable: already stored in this snapshot
        url = cfg.ingest_url_template.format(kind=source.kind, period=source.period)
        part = raw_dir / f"{source.name}.part"
        download = fetcher(url, part)
        reason = check_payload(source, download)
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
        _write_manifest(manifest_path, manifest)  # per file, so a later failure loses nothing

    _write_manifest(manifest_path, manifest)
    return list(manifest.values())


def main(cfg: Config) -> None:
    for entry in ingest(cfg):
        state = f"QUARANTINED ({entry['reason']})" if entry["quarantined"] else "ok"
        print(f"{entry['file']}: {entry['bytes']:,} bytes, {state}")
