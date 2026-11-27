"""Import a CSV / TSV / JSON / NDJSON / Parquet / Excel / Arrow file from an HTTPS URL.

Covers public files, pre-signed object-storage links (Amazon S3, Google Cloud Storage,
Azure Blob SAS) and Google Sheets "export as CSV" links. Every hop is validated with the
destination policy (HTTPS only, no private/loopback/metadata addresses) and pinned to the
validated IP, redirects are followed manually (max 5) and re-validated, an optional bearer
token is only ever sent to the original origin, and the download is capped while streaming.
"""

from collections import OrderedDict
import hashlib
import re
import threading
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from app.adapters.connectors.destination_policy import DestinationPolicy, validate_http_url
from app.adapters.connectors.rest_connector import _PinnedAddressTransport
from app.adapters.parsers.registry import ParserRegistry
from app.application.ports.clock import Clock
from app.application.ports.connectors import (
    DiscoveredTable,
    ExtractionBatch,
    ExtractionRequest,
    ProbeResult,
)
from app.domain.errors import ConnectorError, LimitExceededError, SecurityPolicyError, ValidationError
from app.domain.limits import DEFAULT_DATA_LIMITS, IDENTIFIER_CHARS, DataLimits
from app.domain.models import (
    ConnectorFamily,
    ConsistencyLevel,
    ExtractionCapabilities,
    SnapshotManifest,
    TableSchema,
)

REMOTE_FORMATS = ("csv", "tsv", "json", "ndjson", "parquet", "xlsx", "arrow")

_EXTENSION_FORMATS = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".tab": "tsv",
    ".json": "json",
    ".ndjson": "ndjson",
    ".jsonl": "ndjson",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".xlsx": "xlsx",
    ".arrow": "arrow",
    ".arrows": "arrow",
    ".feather": "arrow",
}

_CONTENT_TYPE_FORMATS = {
    "text/csv": "csv",
    "application/csv": "csv",
    "text/tab-separated-values": "tsv",
    "application/json": "json",
    "application/x-ndjson": "ndjson",
    "application/ndjson": "ndjson",
    "application/jsonl": "ndjson",
    "application/vnd.apache.parquet": "parquet",
    "application/x-parquet": "parquet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.apache.arrow.file": "arrow",
    "application/vnd.apache.arrow.stream": "arrow",
}

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
HTML_SNIFF_BYTES = 4096
MAX_URL_CHARS = 4096
_PROBE_BYTES = 64 * 1024
RECENT_DOWNLOADS_MAX = 4
RECENT_DOWNLOAD_TTL_SECONDS = 600


def detect_remote_format(url: str, content_type: str | None, explicit: str | None) -> str:
    """Pick a parser format from an explicit choice, the URL, or the response Content-Type."""
    if explicit and explicit.lower() not in ("", "auto"):
        fmt = explicit.lower()
        if fmt not in REMOTE_FORMATS:
            raise ValidationError(f"Unsupported file format {explicit!r}", remediation=f"Use one of: {', '.join(REMOTE_FORMATS)}.")
        return fmt
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("format", "output"):
        for value in query.get(key, []):
            if value.lower() in ("csv", "tsv"):
                return value.lower()
    ext = PurePosixPath(unquote(parsed.path)).suffix.lower()
    if ext in _EXTENSION_FORMATS:
        return _EXTENSION_FORMATS[ext]
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in _CONTENT_TYPE_FORMATS:
        return _CONTENT_TYPE_FORMATS[ctype]
    raise ValidationError(
        "Could not tell the file format from the link.",
        remediation="Choose the file format explicitly (CSV, TSV, JSON, NDJSON, Parquet, Excel or Arrow).",
    )


def default_table_name(url: str) -> str:
    stem = PurePosixPath(unquote(urlparse(url).path)).stem
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_").lower()
    if not cleaned or cleaned in ("export", "pub", "download", "file"):
        return "remote_file"
    return cleaned[:IDENTIFIER_CHARS]


@dataclass(frozen=True, slots=True)
class _Download:
    content: bytes
    content_type: str
    final_url: str
    declared_length: int | None


class RemoteFileConnector:
    family = ConnectorFamily.REMOTE_FILE

    def __init__(
        self,
        parser_registry: ParserRegistry,
        clock: Clock,
        policy: DestinationPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._parsers = parser_registry
        self._clock = clock
        self._policy = policy or DestinationPolicy(allow_private_local_for_tests=False, require_https=True)
        self._transport = transport
        self._recent: OrderedDict[str, tuple[float, _Download]] = OrderedDict()
        self._recent_lock = threading.Lock()

    @staticmethod
    def _cache_key(url: str, credentials: dict[str, str]) -> str:
        token = credentials.get("bearer_token", "").strip()
        return hashlib.sha256(f"{url}\n{token}".encode("utf-8")).hexdigest()

    def _remember(self, key: str, download: _Download) -> None:
        with self._recent_lock:
            self._recent[key] = (time.monotonic(), download)
            self._recent.move_to_end(key)
            while len(self._recent) > RECENT_DOWNLOADS_MAX:
                self._recent.popitem(last=False)

    def _take_recent(self, key: str) -> _Download | None:
        with self._recent_lock:
            entry = self._recent.pop(key, None)
        if entry is None or time.monotonic() - entry[0] > RECENT_DOWNLOAD_TTL_SECONDS:
            return None
        return entry[1]

    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            transactional_snapshot=False,
            resumable_cursor=False,
            supports_incremental=False,
        )

    @staticmethod
    def _url(config: dict[str, Any]) -> str:
        url = str(config.get("url", "")).strip()
        if not url:
            raise ValidationError("A file URL is required.", field_errors={"url": "Required"})
        if len(url) > MAX_URL_CHARS:
            raise ValidationError("The file URL is too long.", field_errors={"url": f"At most {MAX_URL_CHARS} characters"})
        return url

    def _download(
        self,
        url: str,
        credentials: dict[str, str],
        limits: DataLimits,
        *,
        head_only: bool = False,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> _Download:
        cap = limits.max_uploaded_file_bytes
        first_origin, _, _, _ = validate_http_url(url, self._policy)
        token = credentials.get("bearer_token", "").strip()
        timeout = httpx.Timeout(
            connect=limits.rest_connect_timeout_seconds,
            read=limits.rest_response_timeout_seconds,
            write=5.0,
            pool=5.0,
        )
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            origin, _, _, resolved_ip = validate_http_url(current, self._policy)
            headers = {"Accept": "*/*"}
            if token and origin == first_origin:
                headers["Authorization"] = f"Bearer {token}"
            with httpx.Client(
                follow_redirects=False,
                timeout=timeout,
                transport=_PinnedAddressTransport(resolved_ip, self._transport),
            ) as client:
                with client.stream("GET", current, headers=headers) as resp:
                    if resp.status_code in _REDIRECT_STATUSES:
                        location = resp.headers.get("location")
                        if not location:
                            raise ConnectorError(f"HTTP {resp.status_code} redirect without a Location header")
                        current = urljoin(current, location)
                        continue
                    if resp.status_code in (401, 403):
                        raise ConnectorError(
                            f"The file server refused access (HTTP {resp.status_code}).",
                            retryable=False,
                            remediation="Check that the link is public, or that the pre-signed URL has not expired.",
                        )
                    if resp.status_code == 404:
                        raise ConnectorError(
                            "No file was found at this link (HTTP 404).",
                            retryable=False,
                            remediation="Copy the link again; pre-signed links stop working after they expire.",
                        )
                    if resp.status_code >= 400:
                        raise ConnectorError(
                            f"The download failed with HTTP {resp.status_code}.",
                            retryable=resp.status_code >= 500 or resp.status_code == 429,
                        )
                    declared_raw = resp.headers.get("content-length", "")
                    declared = int(declared_raw) if declared_raw.isdigit() else None
                    if declared is not None and declared > cap:
                        raise LimitExceededError(
                            f"The file is {declared:,} bytes; the limit is {cap:,} bytes.",
                            remediation="Filter or split the file, or export it as Parquet to make it smaller.",
                        )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        if is_cancelled():
                            raise ConnectorError("Download cancelled")
                        total += len(chunk)
                        if total > cap:
                            raise LimitExceededError(
                                f"The file is larger than the {cap:,} byte limit.",
                                remediation="Filter or split the file, or export it as Parquet to make it smaller.",
                            )
                        chunks.append(chunk)
                        if head_only and total >= _PROBE_BYTES:
                            break
                    return _Download(
                        content=b"".join(chunks),
                        content_type=resp.headers.get("content-type", ""),
                        final_url=current,
                        declared_length=declared,
                    )
        raise SecurityPolicyError(f"The link redirected more than {MAX_REDIRECTS} times.")

    def _parse(self, download: _Download, config: dict[str, Any], limits: DataLimits) -> tuple[str, list[Any]]:
        url = self._url(config)
        try:
            fmt = detect_remote_format(url, download.content_type, config.get("format"))
        except ValidationError:
            if download.final_url == url:
                raise
            fmt = detect_remote_format(download.final_url, download.content_type, config.get("format"))
        if fmt not in REMOTE_FORMATS:
            raise ValidationError(f"Unsupported file format {fmt!r}")
        if not download.content:
            raise ValidationError("The downloaded file is empty.")
        head = download.content[:HTML_SNIFF_BYTES].removeprefix(b"\xef\xbb\xbf").lstrip().lower()
        looks_like_html = head.startswith((b"<!doctype html", b"<html", b"<!--")) or b"<html" in head
        if download.content_type.split(";")[0].strip().lower() == "text/html" or looks_like_html:
            raise ValidationError(
                "This link opens a web page, not a file.",
                remediation="Share the file publicly and use a direct download link (for Google Sheets: Anyone with the link can view).",
            )
        table_name = str(config.get("table_name") or default_table_name(url))
        parser = self._parsers.get(fmt)
        return fmt, parser.parse(download.content, table_name, limits, config.get("options"))

    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult:
        url = self._url(config)
        download = self._download(url, credentials, DEFAULT_DATA_LIMITS, head_only=True)
        try:
            fmt = detect_remote_format(url, download.content_type, config.get("format"))
        except ValidationError:
            fmt = "unknown"
        host = urlparse(download.final_url).hostname or ""
        size = f"{download.declared_length:,} bytes" if download.declared_length is not None else "size not reported"
        return ProbeResult(
            ok=True,
            family=self.family,
            message=f"Reached {host} · {fmt.upper()} · {size}",
            capabilities=self.capabilities(),
            details={"host": host, "format": fmt},
        )

    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]:
        url = self._url(config)
        download = self._download(url, credentials, DEFAULT_DATA_LIMITS)
        self._remember(self._cache_key(url, credentials), download)
        fmt, parsed_tables = self._parse(download, config, DEFAULT_DATA_LIMITS)
        return tuple(
            DiscoveredTable(
                name=pt.table_name,
                columns=pt.columns,
                estimated_rows=pt.row_count,
                metadata={"format": fmt},
            )
            for pt in parsed_tables
        )

    def extract(
        self,
        config: dict[str, Any],
        credentials: dict[str, str],
        request: ExtractionRequest,
        is_cancelled: Callable[[], bool],
    ) -> tuple[Iterable[ExtractionBatch], SnapshotManifest]:
        if is_cancelled():
            raise ConnectorError("Extraction cancelled before start")
        started_at = self._clock.now()
        url = self._url(config)
        download = self._take_recent(self._cache_key(url, credentials)) or self._download(
            url, credentials, request.limits, is_cancelled=is_cancelled
        )
        fmt, parsed_tables = self._parse(download, config, request.limits)

        selected = set(request.selected_tables) if request.selected_tables else None
        if selected is not None and not any(pt.table_name in selected for pt in parsed_tables):
            raise ValidationError(
                f"None of the selected tables were found: {', '.join(sorted(selected))}",
                remediation="Re-run discovery and select from the tables it lists.",
            )

        batches: list[ExtractionBatch] = []
        table_schemas: list[TableSchema] = []
        total_rows = 0
        total_bytes = 0
        hasher = hashlib.sha256()
        for pt in parsed_tables:
            if selected is not None and pt.table_name not in selected:
                continue
            batches.append(
                ExtractionBatch(
                    table_name=pt.table_name,
                    arrow_ipc_bytes=pt.ipc_bytes,
                    row_count=pt.row_count,
                    normalized_bytes=pt.normalized_bytes,
                )
            )
            table_schemas.append(
                TableSchema(
                    table_name=pt.table_name,
                    columns=pt.columns,
                    row_count=pt.row_count,
                    normalized_bytes=pt.normalized_bytes,
                )
            )
            total_rows += pt.row_count
            total_bytes += pt.normalized_bytes
            hasher.update(pt.table_name.encode("utf-8"))
            for col in pt.columns:
                hasher.update(f"{col.name}:{col.arrow_type}".encode("utf-8"))

        if total_bytes > request.limits.max_normalized_source_bytes:
            raise LimitExceededError(
                f"Normalized size {total_bytes} exceeds limit {request.limits.max_normalized_source_bytes}"
            )

        manifest = SnapshotManifest(
            snapshot_id=f"snap_{request.job_id}",
            workspace_id=request.workspace_id,
            source_id=request.source_id,
            version=request.version,
            schema_fingerprint=hasher.hexdigest(),
            tables=tuple(table_schemas),
            total_rows=total_rows,
            total_normalized_bytes=total_bytes,
            consistency_level=ConsistencyLevel.STATIC_EXPORT,
            extraction_started_at=started_at,
            extraction_completed_at=self._clock.now(),
            is_complete=True,
            is_sampled=False,
            artifact_id=f"art_{request.job_id}",
            sha256=hasher.hexdigest(),
            # Host only: pre-signed URLs carry credentials in the query string.
            source_config_summary={
                "family": self.family.value,
                "format": fmt,
                "host": urlparse(url).hostname or "",
            },
        )
        return batches, manifest
