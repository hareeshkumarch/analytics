import hashlib
import json
import random
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlencode, urljoin

import httpx
import pyarrow as pa

from app.adapters.connectors.destination_policy import DestinationPolicy, validate_http_url
from app.adapters.parsers.inference import infer_table
from app.adapters.parsers.registry import serialize_arrow_ipc_checked
from app.application.ports.clock import Clock
from app.application.ports.connectors import (
    DiscoveredTable,
    ExtractionBatch,
    ExtractionRequest,
    ProbeResult,
)
from app.domain.errors import ConnectorError, LimitExceededError, SecurityPolicyError, ValidationError
from app.domain.limits import DataLimits, DEFAULT_DATA_LIMITS
from app.domain.models import (
    ColumnSchema,
    ConnectorFamily,
    ConsistencyLevel,
    ExtractionCapabilities,
    SnapshotManifest,
    TableSchema,
)


def _split_outside_quotes(text: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    start = 0
    in_quote = False
    escaped = False
    for index, ch in enumerate(text):
        if escaped:
            escaped = False
        elif ch == "\\" and in_quote:
            escaped = True
        elif ch == '"':
            in_quote = not in_quote
        elif ch == delimiter and not in_quote:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _next_link_from_header(value: str) -> str | None:
    """Return the rel="next" target of an RFC 8288 Link header (GitHub, Shopify, many others)."""
    if not value:
        return None
    i = 0
    n = len(value)
    while i < n:
        while i < n and value[i] in " ,\t\r\n":
            i += 1
        if i >= n or value[i] != "<":
            i += 1
            continue
        close = value.find(">", i + 1)
        if close == -1:
            break
        target = value[i + 1 : close].strip()
        j = close + 1
        in_quote = False
        escaped = False
        while j < n:
            ch = value[j]
            if escaped:
                escaped = False
            elif ch == "\\" and in_quote:
                escaped = True
            elif ch == '"':
                in_quote = not in_quote
            elif ch == "," and not in_quote:
                break
            j += 1
        params_raw = value[close + 1 : j]
        for param in _split_outside_quotes(params_raw, ";"):
            name, sep, raw_val = param.partition("=")
            if not sep or name.strip().lower() != "rel":
                continue
            rel_val = raw_val.strip()
            if len(rel_val) >= 2 and rel_val[0] == rel_val[-1] and rel_val[0] in ('"', "'"):
                rel_val = rel_val[1:-1]
            if "next" in rel_val.lower().split():
                return target or None
        i = j + 1
    return None


def _extract_by_path(payload: Any, path: str) -> Any:
    if not path or path in ("$", "."):
        return payload
    cur = payload
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ValidationError(f"Collection path {path!r} missing key {part!r}")
        cur = cur[part]
    return cur


class _PinnedAddressTransport(httpx.BaseTransport):
    def __init__(self, resolved_ip: str, delegate: httpx.BaseTransport | None = None) -> None:
        self._resolved_ip = resolved_ip
        self._delegate = delegate or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        original_host = request.url.host
        host_header = request.headers.get("host")
        request.url = request.url.copy_with(host=self._resolved_ip)
        if host_header:
            request.headers["Host"] = host_header
        request.extensions["sni_hostname"] = original_host
        return self._delegate.handle_request(request)

    def close(self) -> None:
        self._delegate.close()


def _cell_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class RestJsonConnector:
    family = ConnectorFamily.REST_JSON

    def __init__(
        self,
        clock: Clock,
        policy: DestinationPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._clock = clock
        self._policy = policy or DestinationPolicy(allow_private_local_for_tests=False, require_https=True)
        self._transport = transport

    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            transactional_snapshot=False,
            resumable_cursor=False,
            supports_incremental=False,
        )

    def _validate_config(self, config: dict[str, Any]) -> tuple[str, str]:
        if not config.get("safe_get_documented", False):
            raise ValidationError("REST endpoint must be documented as safe GET for extraction")
        url = str(config.get("url", "")).strip()
        collection_path = str(config.get("collection_path", "")).strip()
        if not url or not collection_path:
            raise ValidationError("REST connector requires 'url' and explicit 'collection_path'")
        return url, collection_path

    def _fetch_validated(
        self,
        url: str,
        expected_origin: str,
        credentials: dict[str, str],
        limits: DataLimits,
        attempts_remaining: list[int],
    ) -> tuple[dict[str, Any] | list[Any], int, httpx.Headers]:
        origin, _, _, resolved_ip = validate_http_url(url, self._policy)
        if origin != expected_origin:
            raise SecurityPolicyError(
                f"Cross-origin pagination target blocked ({origin} != {expected_origin}); secrets never forwarded."
            )

        headers: dict[str, str] = {"Accept": "application/json"}
        if credentials.get("bearer_token"):
            headers["Authorization"] = f"Bearer {credentials['bearer_token']}"

        timeout = httpx.Timeout(
            connect=limits.rest_connect_timeout_seconds,
            read=limits.rest_response_timeout_seconds,
            write=5.0,
            pool=5.0,
        )
        with httpx.Client(
            follow_redirects=False,
            timeout=timeout,
            transport=_PinnedAddressTransport(resolved_ip, self._transport),
        ) as client:
            for attempt in range(limits.rest_max_attempts_per_request):
                if attempts_remaining[0] <= 0:
                    raise LimitExceededError("REST max HTTP attempts (100) exceeded")
                attempts_remaining[0] -= 1
                resp = client.get(url, headers=headers)
                if 300 <= resp.status_code < 400:
                    raise SecurityPolicyError(f"HTTP redirects are disabled for REST connector ({resp.status_code})")
                if resp.status_code in (429, 502, 503, 504):
                    if attempt == limits.rest_max_attempts_per_request - 1:
                        raise ConnectorError(f"REST upstream transient failure {resp.status_code}", retryable=True)
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else (2 ** attempt)
                    delay = min(10.0, delay + random.uniform(0.0, 0.25))
                    time.sleep(delay)
                    continue
                if resp.status_code >= 400:
                    raise ConnectorError(f"REST upstream returned HTTP {resp.status_code}", retryable=False)

                raw = resp.content
                if len(raw) > limits.rest_max_decoded_bytes_per_response:
                    raise LimitExceededError("REST response exceeds 10,000,000 decoded bytes")
                return resp.json(), len(raw), resp.headers
        raise ConnectorError("REST request failed after retries", retryable=True)

    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult:
        url, _ = self._validate_config(config)
        origin, _, _, _ = validate_http_url(url, self._policy)
        attempts = [3]
        self._fetch_validated(url, origin, credentials, DEFAULT_DATA_LIMITS, attempts)
        return ProbeResult(
            ok=True,
            family=self.family,
            message=f"Validated safe GET endpoint at {origin}",
            capabilities=self.capabilities(),
            details={"origin": origin},
        )

    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]:
        url, collection_path = self._validate_config(config)
        origin, _, _, _ = validate_http_url(url, self._policy)
        attempts = [3]
        payload, _, _ = self._fetch_validated(url, origin, credentials, DEFAULT_DATA_LIMITS, attempts)
        items = _extract_by_path(payload, collection_path)
        if not isinstance(items, list):
            raise ValidationError("collection_path did not resolve to a JSON array")
        keys = list(dict.fromkeys(k for item in items if isinstance(item, dict) for k in item.keys()))
        cols = tuple(ColumnSchema(name=k, arrow_type="string", nullable=True) for k in keys)
        tname = str(config.get("table_name") or "rest_records")
        return (DiscoveredTable(name=tname, columns=cols, estimated_rows=len(items)),)

    def extract(
        self,
        config: dict[str, Any],
        credentials: dict[str, str],
        request: ExtractionRequest,
        is_cancelled: Callable[[], bool],
    ) -> tuple[Iterable[ExtractionBatch], SnapshotManifest]:
        if is_cancelled():
            raise ConnectorError("REST extraction cancelled before start")
        started_at = self._clock.now()
        url, collection_path = self._validate_config(config)
        origin, _, _, _ = validate_http_url(url, self._policy)

        pagination_mode = str(config.get("pagination_mode", "none"))
        cursor_param = str(config.get("cursor_param", "cursor"))
        cursor_path = str(config.get("next_cursor_path", "next_cursor"))
        next_link_path = str(config.get("next_link_path", "next"))

        all_records: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        attempts_remaining = [request.limits.rest_max_http_attempts]
        total_decoded = 0
        current_url: str | None = url
        pages = 0

        while current_url is not None:
            if is_cancelled():
                raise ConnectorError("REST extraction cancelled")
            if pages >= request.limits.rest_max_completed_pages:
                raise LimitExceededError(
                    "REST pagination hit max_completed_pages (50) before documented completion"
                )
            payload, decoded_len, resp_headers = self._fetch_validated(
                current_url, origin, credentials, request.limits, attempts_remaining
            )
            total_decoded += decoded_len
            if total_decoded > request.limits.rest_max_decoded_bytes_per_job:
                raise LimitExceededError("REST job exceeds 80,000,000 decoded bytes")

            items = _extract_by_path(payload, collection_path)
            if not isinstance(items, list):
                raise ValidationError("collection_path did not resolve to a JSON array")
            for item in items:
                if isinstance(item, dict):
                    if len(all_records) >= request.limits.max_rows_per_dataset:
                        raise LimitExceededError("REST dataset exceeds max_rows_per_dataset")
                    all_records.append(item)
            pages += 1

            if pagination_mode == "none":
                current_url = None
            elif pagination_mode == "cursor":
                try:
                    nxt = _extract_by_path(payload, cursor_path)
                except ValidationError:
                    nxt = None
                if not nxt:
                    current_url = None
                else:
                    nxt_str = str(nxt)
                    if nxt_str in seen_cursors:
                        raise ConnectorError(f"Repeated REST pagination cursor detected: {nxt_str!r}")
                    seen_cursors.add(nxt_str)
                    sep = "&" if "?" in url else "?"
                    current_url = f"{url}{sep}{urlencode({cursor_param: nxt_str})}"
            elif pagination_mode == "next_link":
                try:
                    nxt_link = _extract_by_path(payload, next_link_path)
                except ValidationError:
                    nxt_link = None
                if not nxt_link:
                    current_url = None
                else:
                    resolved_next = urljoin(current_url, str(nxt_link))
                    if resolved_next in seen_cursors:
                        raise ConnectorError(f"Repeated REST next_link detected: {resolved_next!r}")
                    seen_cursors.add(resolved_next)
                    current_url = resolved_next
            elif pagination_mode == "link_header":
                header_next = _next_link_from_header(resp_headers.get("link", ""))
                if not header_next:
                    current_url = None
                else:
                    resolved_next = urljoin(current_url, header_next)
                    if resolved_next in seen_cursors:
                        raise ConnectorError(f"Repeated REST Link header detected: {resolved_next!r}")
                    seen_cursors.add(resolved_next)
                    current_url = resolved_next
            elif pagination_mode == "last_id":
                has_more_path = str(config.get("has_more_path", "has_more"))
                id_field = str(config.get("id_field", "id"))
                try:
                    has_more = bool(_extract_by_path(payload, has_more_path))
                except ValidationError:
                    has_more = False
                last_item = items[-1] if items and isinstance(items[-1], dict) else None
                last_id = last_item.get(id_field) if last_item else None
                if not has_more or last_id in (None, ""):
                    current_url = None
                else:
                    last_id_str = str(last_id)
                    if last_id_str in seen_cursors:
                        raise ConnectorError(f"Repeated REST last-id cursor detected: {last_id_str!r}")
                    seen_cursors.add(last_id_str)
                    sep = "&" if "?" in url else "?"
                    current_url = f"{url}{sep}{urlencode({cursor_param: last_id_str})}"
            else:
                raise ValidationError(f"Unsupported pagination_mode: {pagination_mode!r}")

        tname = str(config.get("table_name") or (request.selected_tables[0] if request.selected_tables else "rest_records"))
        keys = list(dict.fromkeys(k for r in all_records for k in r.keys()))
        if keys:
            arrow_table, col_schemas = infer_table(keys, [[_cell_text(r.get(k)) for r in all_records] for k in keys], config.get("date_order"))
        else:
            arrow_table = pa.table({"_empty": pa.array([], type=pa.string())})
            col_schemas = (ColumnSchema(name="_empty", arrow_type="string", nullable=True),)
        ipc_bytes = serialize_arrow_ipc_checked(arrow_table, request.limits)
        digest = hashlib.sha256(ipc_bytes).hexdigest()
        completed_at = self._clock.now()

        batch = ExtractionBatch(
            table_name=tname,
            arrow_ipc_bytes=ipc_bytes,
            row_count=arrow_table.num_rows,
            normalized_bytes=len(ipc_bytes),
        )
        manifest = SnapshotManifest(
            snapshot_id=f"snap_{request.job_id}",
            workspace_id=request.workspace_id,
            source_id=request.source_id,
            version=request.version,
            schema_fingerprint=digest,
            tables=(
                TableSchema(
                    table_name=tname,
                    columns=col_schemas,
                    row_count=arrow_table.num_rows,
                    normalized_bytes=len(ipc_bytes),
                ),
            ),
            total_rows=arrow_table.num_rows,
            total_normalized_bytes=len(ipc_bytes),
            consistency_level=ConsistencyLevel.BEST_EFFORT_WINDOW,
            extraction_started_at=started_at,
            extraction_completed_at=completed_at,
            is_complete=True,
            is_sampled=False,
            artifact_id=f"art_{request.job_id}",
            sha256=digest,
            source_config_summary={"family": self.family.value, "pages": str(pages)},
        )
        return [batch], manifest
