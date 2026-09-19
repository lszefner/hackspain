"""Application-owned sequencing. Supabase is the sole durable runtime store."""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import credentials, settings
from .contracts import Contracts, canonical_bytes, digest
from .deterministic import VERSION as DETERMINISTIC_VERSION
from .deterministic import accept, extract, native_reading, strip_invisible
from .events import emit
from .export import export_bundle
from .manifest import discover
from .normalization import normalize_invoice
from .pdf import extract_text, render_pdf
from .preflight import model_catalogue
from .storage import PostgresRepository, SupabaseStorage
from .validation import complete_annotation_kind_evidence, validate_interpretation


class StageError(Exception):
    def __init__(self, code, *, unknown=False):
        super().__init__(code)
        self.code, self.unknown = code, unknown


class Pipeline:
    def __init__(self, repo, storage, contracts, config, secrets=None):
        self.repo, self.storage, self.contracts, self.config = (
            repo,
            storage,
            contracts,
            config,
        )
        self.secrets = secrets or {}
        self.semaphore = asyncio.Semaphore(config.get("concurrency", 2))
        self.document_semaphore = asyncio.Semaphore(config.get("concurrency", 2))
        self.stopped_providers = set()

    def artifact(self, value, kind, parents=()):
        ref = self.storage.put(canonical_bytes(value), kind, "application/json")
        return self.repo.save_artifact(ref, payload=value, parent_artifact_ids=parents)

    def bytes_artifact(self, value, kind, content_type):
        return self.repo.save_artifact(self.storage.put(value, kind, content_type))

    def load_artifact(self, artifact_id):
        artifact = self.repo.get_artifact(artifact_id)
        if artifact is None:
            raise StageError("artifact_missing")
        data = self.storage.get(artifact["object_key"])
        if digest(data) != artifact["sha256"]:
            raise StageError("artifact_hash_mismatch")
        return json.loads(data)

    async def job(
        self, batch, entry, stage, source_hash, provider, model, operation, extra=None
    ):
        implementation_version = (
            self.config.get("jev_adapter_version", self.config["version"])
            if provider == "jev"
            else self.config["version"]
        )
        job = self.repo.ensure_job(
            batch_id=batch["id"],
            input_id=entry["id"],
            stage=stage,
            input_artifact_hash=source_hash,
            provider=provider,
            model=model,
            config_version=self.config["version"],
            adapter_version=implementation_version,
            prompt_version=implementation_version,
            provider_revision=self.config.get("provider_revision"),
            schema_hash=self.config["schema_hashes"][
                "reading" if stage == "reading" else "invoice"
            ],
            settings={
                "config": self.config,
                "extra": extra or {},
                "input_id": str(entry["id"]),
            },
            max_attempts=self.config.get("max_attempts", 3),
        )
        if job["state"] in ("succeeded", "needs_review"):
            emit(
                "job_reused",
                batch_id=batch["id"],
                input_id=entry["id"],
                job_id=job["id"],
                stage=stage,
                cache_hit=True,
            )
            return self.load_artifact(job["artifact_id"])
        if provider in self.stopped_providers:
            raise StageError("provider_queue_stopped")
        while True:
            async with self.semaphore:
                if provider in self.stopped_providers:
                    raise StageError("provider_queue_stopped")
                current = self.repo.get_job(job["id"])
                recovering = current["state"] == "unknown" and provider == "fal"
                claimed = (
                    self.repo.claim_recovery(job["id"], lease_seconds=900)
                    if recovering
                    else self.repo.claim(job_id=job["id"], lease_seconds=900)
                )
                if not claimed:
                    current = self.repo.get_job(job["id"])
                    if current["state"] in ("succeeded", "needs_review"):
                        return self.load_artifact(current["artifact_id"])
                    raise StageError(
                        current.get("last_error", {}).get("code", current["state"])
                        if current.get("last_error")
                        else current["state"],
                        unknown=current["state"] == "unknown",
                    )
                if recovering:
                    attempt = self.repo.list_attempts(job["id"])[-1]
                    attempt["recover_request_id"] = attempt["provider_request_id"]
                else:
                    attempt = self.repo.begin_attempt(
                        claimed["id"], claimed["lease_token"]
                    )
                started = time.monotonic()
                emit(
                    "attempt_started",
                    batch_id=batch["id"],
                    input_id=entry["id"],
                    job_id=job["id"],
                    attempt_id=attempt["id"],
                    stage=stage,
                    provider=provider,
                    model=model,
                )
                received = False
                try:
                    timeout = (
                        self.config.get("jev_stage_timeout", self.config["timeout"])
                        if provider == "jev"
                        else self.config["timeout"]
                    )
                    if provider == "helmcode-vision":
                        timeout = self.config.get("vision_stage_timeout", 600)
                    value = await asyncio.wait_for(operation(attempt), timeout=timeout)
                    received = True
                    value.setdefault("latency_seconds", time.monotonic() - started)
                    value.setdefault("cost_usd", None)
                    value["attempts"] = attempt["attempt_number"]
                    artifact = self.artifact(value, stage)
                    self.repo.finish_attempt_metadata(
                        attempt["id"],
                        usage=value.get("usage", {}),
                        latency_seconds=value["latency_seconds"],
                    )
                    self.repo.complete(
                        claimed["id"],
                        claimed["lease_token"],
                        artifact["id"],
                        status="needs_review"
                        if value.get("status") == "needs_review"
                        else "succeeded",
                    )
                    emit(
                        "attempt_completed",
                        batch_id=batch["id"],
                        input_id=entry["id"],
                        job_id=job["id"],
                        attempt_id=attempt["id"],
                        stage=stage,
                        provider=provider,
                        latency_seconds=value["latency_seconds"],
                        cost_usd=value["cost_usd"],
                    )
                    return value
                except Exception as exc:
                    code = getattr(exc, "code", type(exc).__name__)
                    status_code = getattr(exc, "status_code", None)
                    if isinstance(exc, httpx.HTTPStatusError):
                        status_code = exc.response.status_code
                    unknown = (
                        received
                        or isinstance(
                            exc,
                            (
                                httpx.ReadTimeout,
                                httpx.WriteTimeout,
                                httpx.ReadError,
                                httpx.WriteError,
                                httpx.RemoteProtocolError,
                                TimeoutError,
                            ),
                        )
                        or getattr(exc, "unknown", False)
                    )
                    if (
                        provider == "fal"
                        and attempt.get("provider_request_id")
                        and code not in ("provider_failed", "invalid_response")
                    ):
                        unknown = True
                    retryable = (
                        isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                        or status_code == 429
                        or (status_code is not None and status_code >= 500)
                    ) and not unknown
                    if status_code in (401, 403):
                        self.stopped_providers.add(provider)
                    delay = min(30, 2 ** attempt["attempt_number"]) + random.random()
                    if isinstance(exc, httpx.HTTPStatusError):
                        try:
                            delay = max(
                                delay,
                                min(
                                    300,
                                    float(exc.response.headers.get("retry-after", "0")),
                                ),
                            )
                        except ValueError:
                            pass
                    failed = self.repo.fail(
                        claimed["id"],
                        claimed["lease_token"],
                        {"code": code},
                        retryable=retryable,
                        unknown=unknown,
                        retry_after_seconds=int(delay) + 1,
                    )
                    emit(
                        "attempt_failed",
                        batch_id=batch["id"],
                        input_id=entry["id"],
                        job_id=job["id"],
                        attempt_id=attempt["id"],
                        stage=stage,
                        provider=provider,
                        error_code=code,
                        state=failed["state"],
                    )
                if failed["state"] != "retry_wait":
                    raise StageError(code, unknown=unknown)
            await asyncio.sleep(int(delay) + 1)

    async def process_input(self, batch, item, reading_override=None):
        async with self.document_semaphore:
            return await self._process_input(batch, item, reading_override)

    async def _process_input(self, batch, item, reading_override=None):
        entry = self.repo.register_input(
            batch["id"],
            item["relative_path"],
            file_name=item["file_id"],
            content_hash=item.get("source_sha256"),
        )
        outcome = {
            "file_id": item["file_id"],
            "status": "failed",
            "error": None,
            "cost_usd": None,
        }
        first = None
        try:
            if item.get("error"):
                raise StageError(item["error"])
            if reading_override is not None:
                reading_result = reading_override
            else:
                first = await self.deterministic(batch, entry, item)
                reading_result = (
                    first
                    if first is not None and first.get("invoice")
                    else await self.read(batch, entry, item)
                )
            outcome.update(reading_result)
            if not reading_result.get("invoice"):
                reading = reading_result["reading"]
                self.contracts.validate("reading", reading)
                if not any(
                    block["text"].strip() or block["rows"]
                    for p in reading["pages"]
                    for block in p["blocks"]
                ):
                    raise StageError("empty_reading")
                interpreted = await self.interpret(batch, entry, reading)
                interpretation_raw = interpreted.pop("raw", None)
                outcome.update(interpreted)
                outcome.setdefault("raw", {})["interpretation"] = interpretation_raw
            outcome["file_id"] = item["file_id"]
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            status = (
                "needs_review"
                if code
                in ("unsupported_size", "candidate_overflow", "question_overflow")
                else "failed"
            )
            outcome.update(
                status=status,
                error={
                    "code": code,
                    "unknown_outcome": bool(getattr(exc, "unknown", False)),
                },
            )
        outcome.setdefault(
            "extraction",
            (first or {}).get("extraction")
            or {
                "route": "vision",
                "deterministic": {
                    "attempted": False,
                    "accepted": False,
                    "gaps": ["no_text_layer_or_cascade_off"],
                },
            },
        )
        if (outcome.get("error") or {}).get("code") in (
            "running",
            "pending",
            "retry_wait",
        ):
            return outcome
        artifact = self.artifact(outcome, "outcome")
        self.repo.set_result(
            entry["id"],
            interpreter=self.config["interpreter"],
            artifact_id=artifact["id"],
            status=outcome["status"],
            error=outcome.get("error"),
        )
        return outcome

    async def deterministic(self, batch, entry, item):
        """First cascade phase: embedded text plus label-anchored fields.

        Returns the merged reading+interpretation outcome when the gate
        accepts, a {"extraction": ...} marker when the gate rejects, and None
        when there is no usable text layer or the cascade is vision-only.
        """
        if self.config.get("extraction_cascade") == "vision-only":
            return None
        if entry.get("object_key"):
            original = self.storage.get(entry["object_key"])
        else:
            original = Path(item["local_path"]).read_bytes()
        if digest(original) != item["source_sha256"]:
            raise StageError("source_hash_mismatch")
        original_artifact = self.bytes_artifact(original, "original", "application/pdf")
        entry = self.repo.register_input(
            batch["id"],
            item["relative_path"],
            file_name=item["file_id"],
            content_hash=item["source_sha256"],
            object_key=original_artifact["object_key"],
            size_bytes=len(original),
        )
        try:
            page_texts = extract_text(original)
        except Exception:  # noqa: BLE001 - any parse failure means "no usable text layer"
            return None
        reading = native_reading(item["file_id"], page_texts)
        if reading is None:
            return None
        invisible = sum(strip_invisible(text)[1] for text in page_texts)
        layout = {
            "version": "alpha-1",
            "source_sha256": item["source_sha256"],
            "pages": [
                {"page": number, "additional_rotation": 0}
                for number in range(1, len(page_texts) + 1)
            ],
            "blocks": {
                f"p{number}-b1": {
                    "page": number,
                    "source": "pdfium_text",
                    "bbox": None,
                    "confidence": None,
                }
                for number in range(1, len(page_texts) + 1)
            },
        }

        async def reading_op(attempt):
            return {
                "reading": reading,
                "layout": layout,
                "raw": {
                    "embedded_text": page_texts,
                    "invisible_chars": invisible,
                },
            }

        try:
            reading_result = await self.job(
                batch,
                entry,
                "reading",
                item["source_sha256"],
                "native-text",
                "pdfium-text/1",
                reading_op,
                {"route": "deterministic"},
            )
        except StageError as exc:
            return {
                "extraction": {
                    "route": "vision",
                    "deterministic": {
                        "attempted": True,
                        "accepted": False,
                        "gaps": [f"deterministic_error:{exc.code}"],
                    },
                }
            }

        async def interpretation_op(attempt):
            extracted_gaps = []
            try:
                extracted = extract(reading)
                extracted_gaps = extracted["gaps"]
                invoice = normalize_invoice(extracted["invoice"])
                invoice["file_id"] = item["file_id"]
                invoice["schema_version"] = "0.1"
                self.contracts.validate("invoice", invoice)
                checks = validate_interpretation(
                    invoice, extracted["evidence"], reading, self.contracts
                )
                accepted, reasons = accept(invoice, checks)
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                return {
                    "invoice": None,
                    "evidence": None,
                    "status": "needs_review",
                    "accepted": False,
                    "gaps": extracted_gaps
                    + [f"deterministic_error:{type(exc).__name__}"],
                    "error": None,
                }
            return {
                "invoice": invoice,
                "evidence": extracted["evidence"],
                **checks,
                "gaps": extracted["gaps"] + reasons,
                "accepted": accepted,
                "status": checks["status"] if accepted else "needs_review",
                "error": None,
            }

        try:
            interpretation = await self.job(
                batch,
                entry,
                "interpretation",
                digest(canonical_bytes(reading)),
                "deterministic",
                DETERMINISTIC_VERSION,
                interpretation_op,
            )
        except StageError as exc:
            return {
                "extraction": {
                    "route": "vision",
                    "deterministic": {
                        "attempted": True,
                        "accepted": False,
                        "gaps": [f"deterministic_error:{exc.code}"],
                    },
                }
            }
        if interpretation.get("accepted"):
            return {
                **reading_result,
                **interpretation,
                "extraction": {
                    "route": "deterministic",
                    "deterministic": {
                        "attempted": True,
                        "accepted": True,
                        "gaps": [],
                    },
                },
            }
        return {
            "extraction": {
                "route": "vision",
                "deterministic": {
                    "attempted": True,
                    "accepted": False,
                    "gaps": interpretation.get("gaps", []),
                },
            }
        }

    async def read(self, batch, entry, item):
        if entry.get("object_key"):
            original = self.storage.get(entry["object_key"])
        else:
            original = Path(item["local_path"]).read_bytes()
        if digest(original) != item["source_sha256"]:
            raise StageError("source_hash_mismatch")
        original_artifact = self.bytes_artifact(original, "original", "application/pdf")
        self.repo.register_input(
            batch["id"],
            item["relative_path"],
            file_name=item["file_id"],
            content_hash=item["source_sha256"],
            object_key=original_artifact["object_key"],
            size_bytes=len(original),
        )
        rendered = render_pdf(original, self.config["dpi"])
        if not rendered:
            raise StageError("pdf_no_pages")
        reading = {
            "schema_version": "0.1",
            "file_id": item["file_id"],
            "capabilities": {"block_kinds": False, "tables": False, "layout": False},
            "pages": [],
        }
        layout = {
            "version": "alpha-1",
            "source_sha256": item["source_sha256"],
            "pages": [],
            "blocks": {},
        }
        native = []
        for page in rendered:
            image = page.pop("image")
            image_artifact = self.bytes_artifact(image, "render", "image/png")
            layout["pages"].append(
                {
                    **page,
                    "image_artifact_id": str(image_artifact["id"]),
                    "image_sha256": image_artifact["sha256"],
                    "additional_rotation": 0,
                }
            )
            # Save page metadata before a remote submission so even partial readings remain inspectable.
            self.artifact(
                {"source_sha256": item["source_sha256"], **layout["pages"][-1]},
                "page",
                (original_artifact["id"], image_artifact["id"]),
            )

            vision = self.config.get("ocr") == "helmcode-vision"

            async def operation(attempt, image=image, page_number=page["page"]):
                if vision:
                    from .providers.vision import VisionReader

                    provider = VisionReader(
                        {**self.config, "api_key": self.secrets["HELMCODE_API_KEY"]},
                        self.request_callback(attempt, provider_name="helmcode-vision"),
                    )
                    return await provider.run(image, page_number)
                return await self.ocr_call(image, attempt)

            result = await self.job(
                batch,
                entry,
                "reading",
                image_artifact["sha256"],
                "helmcode-vision" if vision else "fal",
                self.config["vision_model"] if vision else self.config["fal_model"],
                operation,
                {"page": page["page"]},
            )
            text = result["text"]
            if not isinstance(text, str) or not text.strip():
                raise StageError("empty_ocr_page")
            if vision:
                reading["capabilities"]["block_kinds"] = True
                reading["pages"].append(result["page"])
                layout["blocks"].update(result["layout"]["blocks"])
                layout["pages"][-1]["reader_layout"] = result["layout"]
                native.append(result)
                continue
            block_id = f"p{page['page']}-b1"
            # A literal page is one observable block; no invented semantic layout.
            reading["pages"].append(
                {
                    "page": page["page"],
                    "blocks": [
                        {
                            "id": block_id,
                            "kind": "other",
                            "text": text,
                            "rows": [],
                            "uncertainties": [],
                        }
                    ],
                    "non_text_elements": [],
                }
            )
            layout["blocks"][block_id] = {
                "page": page["page"],
                "source": "fal_output",
                "bbox": None,
                "confidence": None,
            }
            native.append(result)
        self.contracts.validate("reading", reading)
        self.artifact(reading, "canonical-reading", (original_artifact["id"],))
        return {"reading": reading, "layout": layout, "raw": {"ocr": native}}

    def request_callback(self, attempt, *, reuse_completed=False, provider_name=None):
        completed = {}
        if reuse_completed:
            job = self.repo.get_job(attempt["job_id"])
            job_ids = [job["id"]]
            ancestors = sorted(
                (
                    candidate
                    for candidate in self.repo.list_jobs(job["batch_id"])
                    if job["work_key"].startswith(candidate["work_key"] + ":retry:")
                ),
                key=lambda candidate: len(candidate["work_key"]),
                reverse=True,
            )
            for ancestor in ancestors:
                # Retrying a reviewed/completed result is an intentional fresh
                # interpretation, whereas failed/unknown runs can resume subcalls.
                if ancestor["state"] not in ("failed", "unknown"):
                    break
                job_ids.append(ancestor["id"])
            for previous in (
                previous
                for job_id in reversed(job_ids)
                for previous in self.repo.list_attempts(job_id)
            ):
                for artifact_id in previous.get("raw_artifact_ids", []):
                    artifact = self.repo.get_artifact(artifact_id)
                    if artifact and artifact["kind"] == "provider-call-cache":
                        saved = self.load_artifact(artifact_id)
                        completed[saved["request_hash"]] = (
                            artifact_id,
                            saved["response"],
                        )

        async def request(method, url, headers, body):
            persistence_started = time.monotonic()
            request_hash = digest(
                canonical_bytes({"method": method, "url": url, "body": body})
            )
            if reuse_completed and request_hash in completed:
                artifact_id, payload = completed[request_hash]
                self.repo.record_attempt_artifact(attempt["id"], artifact_id)
                return httpx.Response(200, json=payload)
            # Request bodies/prompts and responses are private artifacts. Auth headers and
            # signed URLs are never persisted. Intent already exists in the attempts table.
            request_artifact = self.artifact(
                {"method": method, "body": body}, "provider-request"
            )
            self.repo.record_attempt_artifact(attempt["id"], request_artifact["id"])
            request_persistence_seconds = time.monotonic() - persistence_started
            network_started = time.monotonic()
            async with httpx.AsyncClient(timeout=self.config["timeout"]) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    **({"json": body} if method != "GET" else {}),
                )
            network_seconds = time.monotonic() - network_started
            persistence_started = time.monotonic()
            try:
                raw = self.bytes_artifact(
                    response.content, "provider-response", "application/json"
                )
                self.repo.record_attempt_artifact(attempt["id"], raw["id"])
                if reuse_completed and response.is_success:
                    from .providers.jev import valid_choice_response

                    try:
                        payload = response.json()
                    except ValueError:
                        payload = None
                    if valid_choice_response(body["questions"], payload):
                        cached = self.artifact(
                            {"request_hash": request_hash, "response": payload},
                            "provider-call-cache",
                            (request_artifact["id"], raw["id"]),
                        )
                        self.repo.record_attempt_artifact(attempt["id"], cached["id"])
                        completed[request_hash] = (cached["id"], payload)
            except Exception as exc:
                raise StageError("response_persistence_failed", unknown=True) from exc
            request_id = response.headers.get("x-request-id")
            if request_id and urlsplit(url).hostname != "queue.fal.run":
                self.repo.record_request_id(attempt["id"], request_id)
            emit(
                "provider_request_completed",
                attempt_id=attempt["id"],
                provider="fal"
                if urlsplit(url).hostname == "queue.fal.run"
                else (provider_name or self.config["interpreter"]),
                method=method,
                http_status=response.status_code,
                network_seconds=network_seconds,
                persistence_seconds=request_persistence_seconds
                + time.monotonic()
                - persistence_started,
            )
            return response

        return request

    async def ocr_call(self, image, attempt):
        from .providers.fal_ocr import FalOCR

        def save_request_id(request_id, payload=None):
            attempt["provider_request_id"] = request_id
            self.repo.record_request_id(attempt["id"], request_id)
            metadata = {}
            for key in ("status_url", "response_url"):
                value = (payload or {}).get(key)
                if value:
                    url = urlsplit(value)
                    if (
                        url.scheme != "https"
                        or url.hostname != "queue.fal.run"
                        or url.username
                        or url.password
                        or url.query
                        or url.fragment
                    ):
                        raise StageError("invalid_queue_url", unknown=True)
                    metadata[key] = value
            self.repo.finish_attempt_metadata(attempt["id"], request_metadata=metadata)

        provider = FalOCR(
            {**self.config, "api_key": self.secrets["FAL_KEY"]},
            request=self.request_callback(attempt),
            on_request_id=save_request_id,
        )
        if attempt.get("recover_request_id"):
            return await provider.resume(
                attempt["recover_request_id"], metadata=attempt.get("request_metadata")
            )
        return await provider.run(image)

    async def interpret(self, batch, entry, reading):
        name = self.config["interpreter"]
        model = self.config["deepseek_model" if name == "deepseek" else "jev_model"]

        async def operation(attempt):
            if name == "deepseek":
                from .providers.deepseek import DeepSeek

                provider = DeepSeek(
                    {**self.config, "api_key": self.secrets["HELMCODE_API_KEY"]},
                    request=self.request_callback(attempt),
                )
            else:
                from .providers.jev import Jev

                provider = Jev(
                    {**self.config, "api_key": self.secrets["JEV_API_KEY"]},
                    request=self.request_callback(attempt, reuse_completed=True),
                )
            result = await provider.run(reading, self.contracts.schemas["invoice"])
            # Provider-native bytes survive a normalization/schema failure.
            self.artifact(result, "interpretation-native")
            invoice = normalize_invoice(result["invoice"])
            result["invoice"] = invoice
            invoice["file_id"] = reading["file_id"]
            invoice["schema_version"] = "0.1"
            self.contracts.validate("invoice", invoice)
            result["evidence"] = complete_annotation_kind_evidence(
                invoice, result["evidence"], reading
            )
            checks = validate_interpretation(
                invoice, result["evidence"], reading, self.contracts
            )
            return {**result, **checks, "error": None}

        return await self.job(
            batch,
            entry,
            "interpretation",
            digest(canonical_bytes(reading)),
            name,
            model,
            operation,
        )

    def reading_overrides(self, source_batch_id, manifest):
        source_batch = self.repo.get_batch(source_batch_id)
        existing = {
            r["file_name"]: self.load_artifact(r["artifact_id"])
            for r in self.repo.results(source_batch_id)
            if r.get("artifact_id")
            and r["interpreter"] == source_batch["config"]["interpreter"]
        }
        overrides = {}
        for item in manifest:
            old = existing.get(item["file_id"], {})
            if "reading" not in old:
                raise ValueError("Reading run is incomplete; resume it first")
            overrides[item["file_id"]] = {
                key: old[key] for key in ("reading", "layout") if key in old
            }
            reviews = self.config.get("reading_reviews", {})
            if item["file_id"] in reviews:
                from .review import apply_reading_review

                overrides[item["file_id"]] = apply_reading_review(
                    item,
                    overrides[item["file_id"]],
                    reviews[item["file_id"]],
                    self.contracts,
                )
        return overrides

    async def run(self, batch, overrides=None):
        self.repo.set_batch_status(batch["id"], "running")
        if overrides is None and self.config.get("reading_run"):
            overrides = self.reading_overrides(
                self.config["reading_run"], batch["manifest"]
            )
        tasks = [
            self.process_input(batch, item, (overrides or {}).get(item["file_id"]))
            for item in batch["manifest"]
        ]
        outcomes = await asyncio.gather(*tasks)
        counts = {
            status: sum(row["status"] == status for row in outcomes)
            for status in ("completed", "needs_review", "failed")
        }
        active = any(
            (row.get("error") or {}).get("code") in ("running", "pending", "retry_wait")
            for row in outcomes
        )
        self.repo.set_batch_status(
            batch["id"],
            "running" if active else ("partial" if counts["failed"] else "succeeded"),
        )
        return {"batch_id": str(batch["id"]), "counts": counts}


def runtime(config, contracts, *, needs_ocr=True, resources=None):
    secrets = credentials(config, needs_ocr=needs_ocr)
    repo = PostgresRepository(secrets["SUPABASE_DB_URL"])
    if resources is not None:
        resources.callback(repo.close)
    storage = SupabaseStorage(
        secrets["SUPABASE_URL"],
        secrets["SUPABASE_SECRET_KEY"],
        os.getenv("SUPABASE_STORAGE_BUCKET", "invoice-ingestion-private"),
    )
    if resources is not None:
        resources.callback(storage.client.close)
    return Pipeline(repo, storage, contracts, config, secrets)


async def command(args):
    with ExitStack() as resources:
        return await _command(args, resources)


async def _command(args, resources):
    if args.command in ("ingest", "preflight"):
        contracts = Contracts(args.schema_dir)
        config = settings(
            args.interpreter,
            getattr(args, "dpi", 200),
            getattr(args, "concurrency", 2),
            getattr(args, "ocr", "helmcode-vision"),
        )
        config["schema_hashes"] = contracts.hashes
        config["schemas"] = contracts.schemas
        runner = runtime(config, contracts, resources=resources)
        # A verified private storage write and DB read precede any paid work.
        runner.storage.preflight()
        runner.storage.put(
            canonical_bytes({"preflight": "alpha-1"}), "preflight", "application/json"
        )
        runner.repo.list_jobs(None)
        availability = await model_catalogue(config, runner.secrets)
        if args.command == "preflight":
            return {
                "status": "infrastructure_ready",
                "model": availability,
                "ocr_access": "verified_by_first_page_submission",
                "schema_hashes": contracts.hashes,
            }
        manifest = discover(args.input, args.manifest)
        batch = runner.repo.create_batch(manifest, config)
        emit("batch_created", batch_id=batch["id"], count=len(manifest))
        return await runner.run(batch)
    repo = PostgresRepository(os.getenv("SUPABASE_DB_URL"))
    resources.callback(repo.close)
    batch_id = args.reading_run if args.command == "interpret" else args.batch
    batch = repo.get_batch(batch_id)
    if not batch:
        raise ValueError("Batch not found")
    config = batch["config"]
    if args.command == "status":
        jobs = repo.list_jobs(batch_id)
        return {
            "batch_id": str(batch["id"]),
            "status": batch["status"],
            "jobs": [
                {
                    "id": str(j["id"]),
                    "stage": j["stage"],
                    "state": j["state"],
                    "attempts": j["attempt_count"],
                    "error": j.get("last_error"),
                }
                for j in jobs
            ],
            "results": [
                {"input_id": str(r["input_id"]), "status": r["status"]}
                for r in repo.results(batch_id)
            ],
        }
    # Use frozen schemas on resume/export; changing files cannot alter an accepted run.
    contracts = Contracts.from_snapshot(config["schemas"], config["schema_hashes"])
    if args.command == "export":
        storage = SupabaseStorage(
            bucket=os.getenv("SUPABASE_STORAGE_BUCKET", "invoice-ingestion-private")
        )
        resources.callback(storage.client.close)
        runner = Pipeline(repo, storage, contracts, config)
        result_map = {
            r["file_name"]: runner.load_artifact(r["artifact_id"])
            for r in repo.results(batch_id)
            if r.get("artifact_id") and r["interpreter"] == config["interpreter"]
        }
        outcomes = [
            result_map.get(
                item["file_id"],
                {
                    "file_id": item["file_id"],
                    "status": "failed",
                    "error": {"code": "not_completed"},
                },
            )
            for item in batch["manifest"]
        ]
        return export_bundle(
            args.output, batch["manifest"], config, outcomes, contracts.schemas
        )
    if args.command == "interpret":
        next_config = {
            **config,
            **settings(args.interpreter),
            "reading_run": str(batch["id"]),
        }
        next_config.pop("reading_reviews", None)
        if getattr(args, "reading_review", None):
            review = json.loads(Path(args.reading_review).read_text())
            documents = review["documents"]
            indexed = {doc["file_id"]: doc for doc in documents}
            if len(indexed) != len(documents) or set(indexed) != {
                i["file_id"] for i in batch["manifest"]
            }:
                raise ValueError(
                    "Review must account for each batch input exactly once"
                )
            next_config["reading_reviews"] = indexed
        runner = runtime(next_config, contracts, needs_ocr=False, resources=resources)
        await model_catalogue(next_config, runner.secrets)
        overrides = runner.reading_overrides(batch_id, batch["manifest"])
        next_batch = repo.create_batch(batch["manifest"], next_config)
        return await runner.run(next_batch, overrides)
    if config["interpreter"] == "jev":
        from .providers.jev import VERSION as jev_version

        if config.get("jev_adapter_version") != jev_version:
            raise ValueError(
                "Jev implementation changed; create a new interpret batch using --reading-run "
                "to preserve the old run and reuse its completed readings"
            )
    runner = runtime(
        config, contracts, needs_ocr=not config.get("reading_run"), resources=resources
    )
    repo.reconcile_expired(batch_id=batch_id)
    if args.command == "retry":
        repo.retry_jobs(batch_id, args.stage, include_unknown=args.include_unknown)
    return await runner.run(batch)
