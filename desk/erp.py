from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from rules_ingestion.normalize import norm_fecha, norm_importe, norm_nif


class ErpError(RuntimeError):
    pass


class ErpClient:
    def __init__(self, base_url=None, *, request=None, sleep=time.sleep):
        self.base_url = (base_url or os.environ.get("DESK_ERP_URL", "http://127.0.0.1:8009")).rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.scheme != "http":
            raise ErpError("DESK_ERP_URL debe apuntar al simulador local HTTP")
        self.request = request or urllib.request.urlopen
        self.sleep = sleep
        self.token = None
        self.retries = []
        self.pages = 0
        self.ms = 0

    def _fetch(self, path, *, login=False):
        for attempt in range(5):
            headers = {"X-ERP-Token": self.token} if self.token and not login else {}
            body = urllib.parse.urlencode({"usuario": os.environ.get("DESK_ERP_USER", "alberto"),
                                           "clave": os.environ.get("DESK_ERP_PASSWORD", "FACTURAS2009")}).encode() if login else None
            req = urllib.request.Request(self.base_url + path, data=body, headers=headers)
            try:
                with self.request(req, timeout=10) as response:
                    content = response.read()
                root = ET.fromstring(content)
                if root.tag == "error":
                    raise ErpError(root.findtext("codigo", "ERP_INVALID_RESPONSE"))
                return root
            except urllib.error.HTTPError as exc:
                try:
                    code = ET.fromstring(exc.read()).findtext("codigo", f"HTTP_{exc.code}")
                except ET.ParseError:
                    code = f"HTTP_{exc.code}"
                if code not in ("ORA-00600", "SES-401", "ERP-429"):
                    raise ErpError(code) from None
                if login and code == "SES-401":
                    raise ErpError("SES-401: revisa las credenciales del ERP local") from None
                delay = min(0.25 * 2 ** attempt, 4)
                if code == "ERP-429":
                    retry_after = exc.headers.get("Retry-After", "1")
                    try:
                        delay = max(float(retry_after), 0)
                    except ValueError:
                        delay = max((parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds(), 0)
                self.retries.append({"path": path, "attempt": attempt + 1,
                                     "code": code, "wait_seconds": delay})
                if attempt == 4:
                    raise ErpError("ERP agotado: " + code) from None
                self.sleep(delay)
                if code == "SES-401":
                    self._login()
            except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
                self.retries.append({"path": path, "attempt": attempt + 1,
                                     "code": type(exc).__name__, "wait_seconds": min(0.25 * 2 ** attempt, 4)})
                if attempt == 4:
                    raise ErpError("ERP no disponible o respuesta XML invalida") from None
                self.sleep(min(0.25 * 2 ** attempt, 4))
        raise ErpError("ERP no disponible")

    def _login(self):
        self.token = self._fetch("/erp/login", login=True).findtext("token")
        if not self.token:
            raise ErpError("ERP login sin token")

    def snapshot(self):
        started = time.monotonic()
        self._login()
        entries = []
        expected_total = None
        for page in range(1, 201):
            root = self._fetch(f"/erp/asientos?pagina={page}")
            meta = root.find("meta")
            if meta is None:
                raise ErpError("ERP sin metadatos de paginacion")
            pages = int(meta.findtext("paginas", "0"))
            total = int(meta.findtext("total", "-1"))
            if pages < page or pages > 200 or total < 0 or (expected_total is not None and total != expected_total):
                raise ErpError("ERP cambio durante la descarga o paginacion incompleta")
            expected_total = total
            for node in root.findall("asientos/asiento"):
                entry = {n.tag: n.text for n in node}
                entry["fecha"] = norm_fecha(entry.get("fecha"))
                entry["importe"] = str(norm_importe(entry.get("importe")))
                entry["nif"] = norm_nif(entry.get("nif"))
                entries.append(entry)
            self.pages = page
            if page == pages:
                break
        if len(entries) != expected_total or len({e.get("id") for e in entries}) != len(entries):
            raise ErpError("ERP incompleto: no se decide con una descarga parcial")
        self.ms = int((time.monotonic() - started) * 1000)
        return {"entries": entries, "pages": self.pages, "retries": self.retries, "ms": self.ms}
