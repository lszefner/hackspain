from __future__ import annotations

import math
import random
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from time import sleep as _sleep

_PAGE_CAP = 2 * 1024 * 1024
_RETRYABLE_STATUS = {500, 502, 503, 504}
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urlopen(request, timeout):
    return _OPENER.open(request, timeout=timeout)


def _check_base_url(base_url: str) -> str:
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("ERP base_url must be http or https")
    if parts.username or parts.password:
        raise ValueError("ERP base_url must not embed credentials")
    if parts.query or parts.fragment:
        raise ValueError("ERP base_url must not carry a query or fragment")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("ERP base_url has no host")
    if parts.scheme == "http" and host not in _LOOPBACK:
        raise ValueError("plain http is only allowed for a loopback ERP bridge")
    return base_url.rstrip("/")


def _positive_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _read_capped(response) -> bytes:
    data = response.read(_PAGE_CAP + 1)
    if len(data) > _PAGE_CAP:
        raise ValueError("ERP response exceeds the 2MiB page cap")
    return data


def _parse_xml(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("ERP returned malformed XML") from exc


def _retry_after(headers) -> float:
    raw = headers.get("Retry-After") if headers else None
    try:
        wait = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("ERP rate-limited without a usable Retry-After") from exc
    if not math.isfinite(wait) or wait < 0 or wait > 60:
        raise ValueError("ERP rate-limit wait is out of bounds")
    return wait


def _backoff(attempt: int) -> float:
    return random.uniform(0, min(4.0, 0.5 * (2 ** attempt)))


def _login(base: str, username: str, password: str, timeout: float,
           max_attempts: int) -> str:
    body = urllib.parse.urlencode({"usuario": username, "clave": password}).encode()
    request = urllib.request.Request(
        base + "/erp/login", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                root = _parse_xml(_read_capped(response))
            token = root.findtext("./token")
            if not token:
                raise ValueError("ERP login failed")
            return token
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (400, 401):
                raise ValueError("ERP login failed") from exc
            if exc.code == 429:
                wait = _retry_after(exc.headers)
                if attempt < max_attempts:
                    _sleep(wait)
                continue
            if exc.code not in _RETRYABLE_STATUS:
                raise ValueError("ERP login failed") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < max_attempts:
            _sleep(_backoff(attempt))
    raise ValueError("ERP login failed") from last_error


def _fetch_page(base: str, page: int, creds: tuple, token: str,
                timeout: float, max_attempts: int) -> tuple[ET.Element, str]:
    username, password = creds
    url = f"{base}/erp/asientos?pagina={page}"
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(url, headers={"X-ERP-Token": token})
        try:
            with urlopen(request, timeout=timeout) as response:
                return _parse_xml(_read_capped(response)), token
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 401:
                if attempt < max_attempts:
                    token = _login(base, username, password, timeout, max_attempts)
            elif exc.code == 429:
                wait = _retry_after(exc.headers)
                if attempt < max_attempts:
                    _sleep(wait)
                continue
            elif exc.code not in _RETRYABLE_STATUS:
                raise ValueError(f"ERP request failed with status {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < max_attempts:
            _sleep(_backoff(attempt))
    raise ValueError(f"ERP page {page} failed after {max_attempts} attempts") from last_error


def _meta(root: ET.Element) -> dict:
    meta = {}
    for field in ("total", "paginas", "pagina", "por_pagina"):
        text = root.findtext(f"./meta/{field}")
        try:
            meta[field] = int(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"ERP meta field {field} is missing or invalid") from exc
    return meta


def _rows(root: ET.Element) -> list[dict]:
    rows = []
    for node in root.findall("./asientos/asiento"):
        row = {}
        missing = False
        for field in ("id", "fecha", "proveedor", "nif", "pedido", "importe", "estado"):
            value = node.findtext(f"./{field}")
            if value is None:
                missing = True
            row[field] = value or ""
        if missing:
            raise ValueError("ERP row is missing required fields")
        row["id"] = row["id"].strip()
        rows.append(row)
    return rows


def fetch_snapshot(base_url: str, *, username: str, password: str,
                   timeout: float = 10, max_attempts: int = 4,
                   max_pages: int = 10000) -> dict:
    base = _check_base_url(base_url)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) \
            or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    max_attempts = _positive_int(max_attempts, "max_attempts")
    max_pages = _positive_int(max_pages, "max_pages")
    creds = (username, password)
    token = _login(base, username, password, timeout, max_attempts)
    records: list[dict] = []
    seen_ids = set()
    expected_total = expected_pages = expected_per = None
    page = 1
    while True:
        if page > max_pages:
            raise ValueError("ERP snapshot exceeds max_pages")
        root, token = _fetch_page(base, page, creds, token, timeout, max_attempts)
        meta = _meta(root)
        if meta["pagina"] != page:
            raise ValueError("ERP returned a non-contiguous page")
        if meta["total"] < 0 or meta["paginas"] < 1 or meta["por_pagina"] < 1:
            raise ValueError("ERP meta values are out of bounds")
        if meta["paginas"] != max(1, math.ceil(meta["total"] / meta["por_pagina"])):
            raise ValueError("ERP meta pagination is inconsistent")
        if expected_total is None:
            expected_total, expected_pages = meta["total"], meta["paginas"]
            expected_per = meta["por_pagina"]
            if expected_pages > max_pages:
                raise ValueError("ERP snapshot exceeds max_pages")
        elif (meta["total"] != expected_total or meta["paginas"] != expected_pages
              or meta["por_pagina"] != expected_per):
            raise ValueError("ERP totals changed mid-download")
        rows = _rows(root)
        expected_rows = (
            expected_per if page < expected_pages
            else expected_total - (expected_pages - 1) * expected_per
        )
        if len(rows) != expected_rows:
            raise ValueError("ERP page row count mismatch")
        for row in rows:
            if not row["id"]:
                raise ValueError("ERP row has an empty id")
            if row["id"] in seen_ids:
                raise ValueError(f"ERP duplicate row id {row['id']!r}")
            seen_ids.add(row["id"])
        records.extend(rows)
        if page >= expected_pages:
            break
        page += 1
    if len(records) != expected_total:
        raise ValueError("ERP record count does not match meta total")
    return {
        "schema_version": "1.0",
        "source_url": base,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "records": records,
    }
