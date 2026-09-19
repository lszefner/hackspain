import urllib.error
import urllib.request
from email.message import Message

import pytest

from payments import erp


def login_xml(token="tok1"):
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        f"<sesion><token>{token}</token></sesion>"
    ).encode("iso-8859-1")


def page_xml(page, pages, total, per, rows):
    body = "".join(
        "<asiento>"
        f"<id>{r['id']}</id><fecha>{r['fecha']}</fecha>"
        f"<proveedor>{r['proveedor']}</proveedor><nif>{r['nif']}</nif>"
        f"<pedido>{r['pedido']}</pedido><importe>{r['importe']}</importe>"
        f"<estado>{r['estado']}</estado></asiento>"
        for r in rows
    )
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?><respuesta>'
        f"<meta><total>{total}</total><paginas>{pages}</paginas>"
        f"<pagina>{page}</pagina><por_pagina>{per}</por_pagina></meta>"
        f"<asientos>{body}</asientos></respuesta>"
    ).encode("iso-8859-1")


def row(i, estado="PENDIENTE", importe="1.234,56", pedido="PO1"):
    return {
        "id": f"AS-{i:05d}",
        "fecha": "01/09/2026",
        "proveedor": "P1",
        "nif": "B12345678",
        "pedido": pedido,
        "importe": importe,
        "estado": estado,
    }


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        return self.data[:n] if n >= 0 else self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("http://x", code, "err", headers, None)


class FakeERP:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append(request)
        result = self.handler(request, self.calls)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


@pytest.fixture()
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(erp, "_sleep", slept.append)
    return slept


def run_fetch(handler, monkeypatch, **kwargs):
    fake = FakeERP(handler)
    monkeypatch.setattr(erp, "urlopen", fake)
    return fake


def test_two_pages_iso_chars_and_commas(monkeypatch, no_sleep):
    rows1 = [row(i) for i in range(1, 21)]
    rows2 = [row(21, estado="PAGADA", importe="2.500,00")]

    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        page = int(request.full_url.rsplit("pagina=", 1)[1])
        return page_xml(page, 2, 21, 20, rows1 if page == 1 else rows2)

    run_fetch(handler, monkeypatch)
    snap = erp.fetch_snapshot("http://127.0.0.1:8009", username="u", password="p")
    assert snap["schema_version"] == "1.0"
    assert len(snap["records"]) == 21
    assert snap["records"][20]["importe"] == "2.500,00"
    assert "p" != snap["source_url"]


def test_500_retries_same_page(monkeypatch, no_sleep):
    attempts = {"n": 0}

    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        attempts["n"] += 1
        if attempts["n"] == 1:
            return http_error(500)
        return page_xml(1, 1, 1, 20, [row(1)])

    run_fetch(handler, monkeypatch)
    snap = erp.fetch_snapshot("http://localhost:8009", username="u", password="p")
    assert len(snap["records"]) == 1
    assert attempts["n"] == 2


def test_429_retry_after_honored(monkeypatch, no_sleep):
    attempts = {"n": 0}

    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        attempts["n"] += 1
        if attempts["n"] == 1:
            return http_error(429, retry_after=2)
        return page_xml(1, 1, 1, 20, [row(1)])

    run_fetch(handler, monkeypatch)
    erp.fetch_snapshot("http://localhost:8009", username="u", password="p")
    assert no_sleep == [2]


def test_expired_token_relogins(monkeypatch, no_sleep):
    logins = {"n": 0}
    hits = {"n": 0}

    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            logins["n"] += 1
            return login_xml(f"tok{logins['n']}")
        hits["n"] += 1
        if hits["n"] == 1:
            return http_error(401)
        assert request.headers["X-erp-token"] == "tok2"
        return page_xml(1, 1, 1, 20, [row(1)])

    run_fetch(handler, monkeypatch)
    snap = erp.fetch_snapshot("http://localhost:8009", username="u", password="p")
    assert len(snap["records"]) == 1
    assert logins["n"] == 2


def test_permanent_400_not_retried(monkeypatch, no_sleep):
    hits = {"n": 0}

    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        hits["n"] += 1
        return http_error(400)

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p")
    assert hits["n"] == 1


def test_malformed_xml_rejected(monkeypatch, no_sleep):
    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        return b"<respuesta><meta>"

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p")


def test_count_mismatch_rejected(monkeypatch, no_sleep):
    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        return page_xml(1, 1, 5, 20, [row(1)])

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p")


def test_duplicate_ids_rejected(monkeypatch, no_sleep):
    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        return page_xml(1, 1, 2, 20, [row(1), row(1)])

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p")


def test_changing_totals_rejected(monkeypatch, no_sleep):
    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        page = int(request.full_url.rsplit("pagina=", 1)[1])
        total = 40 if page == 1 else 41
        rows = [row(i) for i in range(1, 21)] if page == 1 else [row(21)]
        return page_xml(page, 2, total, 20, rows)

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p")


def test_repeated_outage_stops_at_max_attempts(monkeypatch, no_sleep):
    hits = {"n": 0}

    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        hits["n"] += 1
        return http_error(500)

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p",
                           max_attempts=3)
    assert hits["n"] == 3


def test_credentials_never_in_snapshot_or_errors(monkeypatch, no_sleep):
    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            assert b"clave=s3cr3t" in (request.data or b"")
            return http_error(400)
        return b""

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError) as exc:
        erp.fetch_snapshot("http://localhost:8009", username="u",
                           password="s3cr3t")
    assert "s3cr3t" not in str(exc.value)


def test_base_url_validation():
    with pytest.raises(ValueError):
        erp.fetch_snapshot("ftp://x", username="u", password="p")
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://user:pw@127.0.0.1:8009",
                           username="u", password="p")
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://example.com", username="u", password="p")
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://127.0.0.1:8009/?token=x",
                           username="u", password="p")


def test_https_remote_allowed(monkeypatch, no_sleep):
    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        return page_xml(1, 1, 1, 20, [row(1)])

    run_fetch(handler, monkeypatch)
    snap = erp.fetch_snapshot("https://erp.example.com", username="u",
                              password="p")
    assert len(snap["records"]) == 1


def test_no_partial_snapshot_on_failure(monkeypatch, no_sleep):
    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        page = int(request.full_url.rsplit("pagina=", 1)[1])
        if page == 1:
            return page_xml(1, 2, 21, 20, [row(i) for i in range(1, 21)])
        return http_error(400)

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p")

def test_single_login_across_pages(monkeypatch, no_sleep):
    logins = {"n": 0}
    rows = [row(i) for i in range(1, 21)]

    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            logins["n"] += 1
            return login_xml()
        page = int(request.full_url.rsplit("pagina=", 1)[1])
        return page_xml(page, 2, 21, 20, rows if page == 1 else [row(21)])

    run_fetch(handler, monkeypatch)
    erp.fetch_snapshot("http://localhost:8009", username="u", password="p")
    assert logins["n"] == 1


def test_relogin_token_reused(monkeypatch, no_sleep):
    logins = {"n": 0}
    hits = {"n": 0}

    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            logins["n"] += 1
            return login_xml(f"tok{logins['n']}")
        hits["n"] += 1
        page = int(request.full_url.rsplit("pagina=", 1)[1])
        if page == 2 and hits["n"] == 2:
            return http_error(401)
        expected = "tok1" if page == 1 else "tok2"
        assert request.headers["X-erp-token"] == expected
        rows = [row(i) for i in range(1, 21)] if page == 1 else [row(21)]
        return page_xml(page, 2, 21, 20, rows)

    run_fetch(handler, monkeypatch)
    snap = erp.fetch_snapshot("http://localhost:8009", username="u", password="p")
    assert len(snap["records"]) == 21
    assert logins["n"] == 2


def test_repeated_401_stops(monkeypatch, no_sleep):
    logins = {"n": 0}
    hits = {"n": 0}

    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            logins["n"] += 1
            return login_xml()
        hits["n"] += 1
        return http_error(401)

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p",
                           max_attempts=3)
    assert hits["n"] == 3
    assert logins["n"] == 3


def test_login_rate_limit_honored(monkeypatch, no_sleep):
    logins = {"n": 0}

    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            logins["n"] += 1
            if logins["n"] == 1:
                return http_error(429, retry_after=1)
            return login_xml()
        return page_xml(1, 1, 1, 20, [row(1)])

    run_fetch(handler, monkeypatch)
    erp.fetch_snapshot("http://localhost:8009", username="u", password="p")
    assert no_sleep == [1]


def test_login_auth_failure_immediate(monkeypatch, no_sleep):
    hits = {"n": 0}

    def handler(request, calls):
        hits["n"] += 1
        return http_error(401)

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p")
    assert hits["n"] == 1


@pytest.mark.parametrize("retry_after", ["-1", "nan", "abc", "61"])
def test_bad_retry_after_fails(monkeypatch, no_sleep, retry_after):
    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        return http_error(429, retry_after=retry_after)

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p")


def test_per_page_size_change_rejected(monkeypatch, no_sleep):
    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        page = int(request.full_url.rsplit("pagina=", 1)[1])
        if page == 1:
            return page_xml(1, 2, 21, 20, [row(i) for i in range(1, 21)])
        return page_xml(2, 2, 21, 10, [row(21)])

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p")


def test_inconsistent_meta_rejected(monkeypatch, no_sleep):
    def handler(request, calls):
        if request.full_url.endswith("/erp/login"):
            return login_xml()
        return page_xml(1, 3, 21, 20, [row(i) for i in range(1, 21)])

    run_fetch(handler, monkeypatch)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://localhost:8009", username="u", password="p")


def test_invalid_limits_rejected():
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://x", username="u", password="p", timeout=0)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://x", username="u", password="p",
                           max_attempts=True)
    with pytest.raises(ValueError):
        erp.fetch_snapshot("http://x", username="u", password="p",
                           max_pages=0)
