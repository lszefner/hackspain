from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ingestion.contextual_prompt import PROMPT_VERSION, SYSTEM_PROMPT
from ingestion.contracts import canonical_bytes, digest
from ingestion.storage import SupabaseStorage

from . import evaluator, execution_context
from .contextual_contracts import (
    RESPONSE_SCHEMA,
    REVIEW_SCHEMA,
    ReviewProviderError,
    validate,
)
from .contextual_review import (
    PROJECTION_LIMITATION,
    DecisionContextV2Adapter,
    ProviderReply,
    ReviewLimits,
    ReviewProvider,
    _source_documents,
    _validate_response,
    build_review_request,
    review_evaluation,
)
from .decision_context import ContextBundle, ContextError, SourceSnapshot, build_context
from .decision_storage import _jsonable
from .engine_storage import ArchiveError, EngineRepository, EngineStore


@dataclass(frozen=True)
class RuleSource:
    name: str
    content: bytes
    content_type: str = 'application/octet-stream'


class InvoiceDecisionEngine:
    def __init__(self, repository: EngineRepository, storage):
        self.repository, self.storage = repository, storage
        self.archive = EngineStore(repository, storage)

    @classmethod
    def from_supabase(cls) -> InvoiceDecisionEngine:
        storage = SupabaseStorage(bucket=os.environ.get('SUPABASE_STORAGE_BUCKET', 'invoice-ingestion-private'))
        repository = None
        try:
            storage.preflight()
            repository = EngineRepository()
            repository.preflight()
            return cls(repository, storage)
        except Exception:
            if repository is not None:
                repository.close()
            storage.client.close()
            raise

    def close(self):
        try:
            self.repository.close()
        finally:
            client = getattr(self.storage, 'client', None)
            if client is not None and hasattr(client, 'close'):
                client.close()

    def _json(self, ref):
        return json.loads(self.archive.get(ref))

    def _put_json(self, value, kind, parents=()):
        return self.archive.put(canonical_bytes(value), kind, parents=parents)

    def _extraction_graph(self, row, outcome, original_ref, outcome_ref):
        ids = {original_ref['artifact_id'], outcome_ref['artifact_id']}
        jobs = []
        for job in self.repository.list_jobs(row['batch_id']):
            if str(job['input_id']) != str(row['id']):
                continue
            if job.get('artifact_id'):
                ids.add(str(job['artifact_id']))
            attempts = []
            for attempt in self.repository.list_attempts(job['id']):
                ids.update(str(value) for value in attempt.get('raw_artifact_ids', []))
                attempts.append({key: _jsonable(attempt.get(key)) for key in (
                    'id', 'attempt_number', 'provider_request_id', 'status', 'usage',
                    'latency_seconds', 'raw_artifact_ids')})
            jobs.append({key: _jsonable(job.get(key)) for key in (
                'id', 'stage', 'provider', 'model', 'state', 'attempt_count', 'artifact_id')}
                | {'attempts': sorted(attempts, key=lambda a: a['attempt_number'])})
        for page in (outcome.get('layout') or {}).get('pages', []):
            if page.get('image_artifact_id'):
                ids.add(str(page['image_artifact_id']))
        refs = {}
        while ids:
            artifact_id = ids.pop()
            if artifact_id in refs:
                continue
            refs[artifact_id] = self.archive.ref(artifact_id)
            artifact = self.repository.get_artifact(artifact_id)
            ids.update(str(value) for value in artifact.get('parent_artifact_ids', []))
        return sorted(refs.values(), key=lambda r: r['artifact_id']), sorted(jobs, key=lambda j: j['id'])

    def evaluate(self, *, input_id: str, interpreter: str, ruleset: bytes,
                 rule_sources: list[RuleSource], snapshots: dict[str, SourceSnapshot],
                 evaluation_date: str, captured_at: str) -> dict:
        if not isinstance(ruleset, bytes) or not ruleset:
            raise ContextError('exact ruleset bytes are required')
        if not rule_sources or any(
            not isinstance(source, RuleSource) or not isinstance(source.name, str) or not source.name.strip()
            or not isinstance(source.content, bytes) or not source.content
            or not isinstance(source.content_type, str) or not source.content_type.strip()
            for source in rule_sources
        ) or len({source.name for source in rule_sources}) != len(rule_sources):
            raise ContextError('distinct named ruleset source bytes are required')
        row = self.repository.extraction(input_id, interpreter)
        if row is None or row['outcome_status'] not in ('completed', 'needs_review'):
            raise ContextError('a completed durable extraction is required')
        if not row.get('object_key') or not row.get('content_hash') or not row.get('outcome_artifact_id'):
            raise ContextError('extraction lacks durable original or outcome')
        original = self.repository.original(row['object_key'])
        if original is None:
            raise ContextError('original PDF artifact is missing')
        original_ref = self.archive.ref(str(original['id']))
        if original_ref['sha256'] != row['content_hash'] or original_ref['byte_size'] != row['size_bytes']:
            raise ContextError('original PDF does not match input')
        outcome_ref = self.archive.ref(str(row['outcome_artifact_id']))
        if outcome_ref['kind'] != 'outcome':
            raise ContextError('extraction result is not an outcome artifact')
        outcome = self._json(outcome_ref)
        file_id = row['file_name']
        if any((outcome if name is None else outcome.get(name) or {}).get('file_id') != file_id
               for name in (None, 'invoice', 'reading')):
            raise ContextError('extraction file identity mismatch')
        alignment = build_context(outcome, snapshots=snapshots, ruleset=ruleset,
                                  evaluation_date=evaluation_date, captured_at=captured_at)
        bundle = execution_context.prepare_context(alignment)
        result = evaluator.evaluate(bundle).to_dict()
        refs, jobs = self._extraction_graph(row, outcome, original_ref, outcome_ref)
        provenance = self._put_json({'input_id': str(row['id']), 'jobs': jobs, 'artifacts': refs},
                                    'engine-extraction-provenance', [ref['artifact_id'] for ref in refs])
        lineage = []
        for source in sorted(rule_sources, key=lambda s: s.name):
            ref = self.archive.put(source.content, 'engine-rule-source', source.content_type)
            lineage.append({'name': source.name, **ref})
        source_refs = {}
        for source_id, source in bundle.context['sources'].items():
            key = source['artifact_ref']
            if key is None:
                continue
            ref = self.archive.put(bundle.artifacts[key], 'decision-source-' + source['kind'],
                                   'application/octet-stream')
            if ref['object_key'] != key or ref['sha256'] != source['sha256']:
                raise ContextError('context source does not match archive')
            source_refs[source_id] = ref
        packet = {
            'schema_version': 'core-engine-record/1', 'kind': 'evaluation',
            'input_id': str(row['id']), 'batch_id': str(row['batch_id']), 'file_id': file_id,
            'interpreter': interpreter, 'parent_record_id': None,
            'evaluation_id': result['evaluation_id'], 'decision': result['preliminary_decision'],
            'evaluation_date': evaluation_date, 'captured_at': captured_at,
            'original': original_ref, 'outcome': outcome_ref,
            'extraction_provenance': provenance, 'extraction_artifacts': refs,
            'rule_source_lineage': {'basis': 'caller_attested', 'sources': lineage},
            'alignment_context': self._put_json(alignment.context, 'engine-alignment-context'),
            'context': self._put_json(bundle.context, 'engine-context', [ref['artifact_id'] for ref in source_refs.values()]),
            'evaluation': self._put_json(result, 'engine-evaluation-result'),
            'context_schema': self._put_json(execution_context.load_schema(), 'engine-context-schema'),
            'evaluation_schema': self._put_json(evaluator.load_schema(), 'engine-evaluation-schema'),
            'sources': source_refs,
        }
        return self.archive.save_packet(packet)

    def _materialize(self, packet):
        context = self._json(packet['context'])
        artifacts = {}
        expected = {name for name, source in context['sources'].items() if source['artifact_ref'] is not None}
        if set(packet['sources']) != expected:
            raise ContextError('engine context source accounting mismatch')
        for name, ref in packet['sources'].items():
            source = context['sources'][name]
            if source['artifact_ref'] != ref['object_key'] or source['sha256'] != ref['sha256']:
                raise ContextError('engine context source identity mismatch')
            artifacts[ref['object_key']] = self.archive.get(ref)
        bundle = ContextBundle(context=context, artifacts=artifacts)
        result = self._json(packet['evaluation'])
        evaluator.validate_evaluation(result, bundle)
        if canonical_bytes(evaluator.evaluate(bundle).to_dict()) != canonical_bytes(result):
            raise ContextError('stored engine evaluation does not match replay')
        if (result['evaluation_id'] != packet['evaluation_id'] or result['preliminary_decision'] != packet['decision']
                or context['file_id'] != packet['file_id'] or context['evaluation_date'] != packet['evaluation_date']):
            raise ContextError('engine evaluation header mismatch')
        return bundle, result

    def load(self, record_id: str) -> dict:
        packet = self.archive.load_packet(record_id)
        if packet['kind'] == 'review':
            self._load_review(packet)
            return packet
        bundle, _result = self._materialize(packet)
        alignment = ContextBundle(context=self._json(packet['alignment_context']), artifacts=bundle.artifacts)
        if execution_context.prepare_context(alignment).context != bundle.context:
            raise ContextError('stored alignment context mismatch')
        if (self._json(packet['context_schema']) != execution_context.load_schema()
                or self._json(packet['evaluation_schema']) != evaluator.load_schema()):
            raise ContextError('engine schema version mismatch')
        self.archive.get(packet['original'])
        outcome = self._json(packet['outcome'])
        for name in ('invoice', 'reading', 'evidence'):
            source = bundle.context['sources'][name]
            if source['artifact_ref'] is not None and canonical_bytes(outcome.get(name)) != bundle.artifacts[source['artifact_ref']]:
                raise ContextError('outcome does not match frozen decision source')
        if outcome.get('file_id') != packet['file_id']:
            raise ContextError('outcome file identity mismatch')
        for ref in packet['extraction_artifacts']:
            self.archive.get(ref)
        provenance = self._json(packet['extraction_provenance'])
        if provenance['input_id'] != packet['input_id'] or provenance['artifacts'] != packet['extraction_artifacts']:
            raise ContextError('extraction provenance mismatch')
        if packet['original'] not in packet['extraction_artifacts'] or packet['outcome'] not in packet['extraction_artifacts']:
            raise ContextError('extraction provenance lacks original or outcome')
        lineage = packet['rule_source_lineage']
        if lineage['basis'] != 'caller_attested' or not lineage['sources']:
            raise ContextError('ruleset source lineage is missing')
        for source in lineage['sources']:
            self.archive.get({key: value for key, value in source.items() if key != 'name'})
        return packet

    def _load_review(self, packet):
        parent = self.archive.load_packet(packet['parent_record_id'])
        if parent['kind'] != 'evaluation':
            raise ContextError('review parent is not an evaluation')
        self.load(parent['record_id'])
        bundle, evaluation = self._materialize(parent)
        review = self._json(packet['review'])
        validate(REVIEW_SCHEMA, review, 'invalid_review')
        if self._json(packet['review_schema']) != REVIEW_SCHEMA:
            raise ContextError('review schema mismatch')
        if ('rv_' + digest(canonical_bytes({key: value for key, value in review.items() if key != 'review_id'})) != review['review_id']
                or any(packet[key] != parent[key] for key in ('input_id', 'batch_id', 'file_id', 'evaluation_id', 'decision'))
                or review['evaluation_id'] != evaluation['evaluation_id']
                or review['context_id'] != bundle.context['context_id']
                or review['context_sha256'] != evaluation['context_sha256']
                or review['context_schema_version'] != bundle.context['schema_version']
                or review['reviewed_at'] != packet['reviewed_at']
                or review['original_preliminary_decision'] != packet['decision']):
            raise ContextError('review identity mismatch')
        config = self._json(packet['review_config'])
        if (digest(canonical_bytes(config)) != packet['request_sha256'] or config['record_id'] != parent['record_id']
                or config['reviewed_at'] != review['reviewed_at']
                or config['provider'] != review['reviewer']['provider']
                or config['model'] != review['reviewer']['requested_model']
                or config['prompt_version'] != review['reviewer']['prompt_version']
                or config['prompt_sha256'] != review['reviewer']['prompt_sha256']
                or digest(config['system_prompt'].encode()) != config['prompt_sha256']):
            raise ContextError('review request configuration mismatch')
        view = DecisionContextV2Adapter().inspect(bundle.context, bundle.artifacts)
        documents, unavailable, binary, _partial = _source_documents(view, bundle.artifacts)
        projected = False
        if config['prompt_version'] == 'contextual-review/1':
            expected_request = {'system_prompt': config['system_prompt'], 'response_schema': config['response_schema'],
                                'evaluation': evaluation, 'context': bundle.context, 'sources': documents,
                                'unavailable_sources': unavailable, 'unreviewable_sources': binary}
        else:
            expected_request, projected = build_review_request(
                evaluation, bundle.context, view, documents, unavailable, binary, ReviewLimits(**config['limits']),
                system_prompt=config['system_prompt'], response_schema=config['response_schema'])
        if projected and (review['status'] == 'COMPLETED' or not review['attention_required']
                          or PROJECTION_LIMITATION not in review['limitations']):
            raise ContextError('projected review overstates source coverage')
        if digest(canonical_bytes(expected_request)) != review['reviewer']['request_sha256']:
            raise ContextError('review request identity mismatch')
        if review['status'] != 'FAILED' and (packet['provider_request'] is None or packet['provider_response'] is None):
            raise ContextError('completed review lacks provider artifacts')
        if packet['provider_request'] is not None:
            request_bytes = self.archive.get(packet['provider_request'])
            if digest(request_bytes) != review['reviewer']['request_sha256']:
                raise ContextError('review request artifact mismatch')
            request = json.loads(request_bytes)
            if request != expected_request:
                raise ContextError('review request input mismatch')
        if packet['provider_response'] is not None:
            response = self._json(packet['provider_response'])
            if review['status'] != 'FAILED':
                _validate_response(response['payload'], evaluation, documents)
                if projected:
                    _validate_response(response['payload'], evaluation, expected_request['sources'],
                                       source_projections=expected_request['source_projections'])
                if (response['payload']['rule_reviews'] != review['rule_reviews']
                        or response['payload']['findings'] != review['findings']
                        or response['model'] != review['reviewer']['resolved_model']
                        or response['request_id'] != review['reviewer']['request_id']):
                    raise ContextError('review response mismatch')

    async def review(self, record_id: str, *, provider: ReviewProvider,
                     request_key: str, reviewed_at: str, limits: ReviewLimits | None = None) -> dict:
        validate({'type': 'string', 'minLength': 1, 'maxLength': 200}, request_key, 'invalid_request_key')
        validate({'type': 'string', 'format': 'date-time'}, reviewed_at, 'invalid_reviewed_at')
        validate({'type': 'string', 'minLength': 1}, provider.provider, 'invalid_provider')
        validate({'type': 'string', 'minLength': 1}, provider.model, 'invalid_provider')
        endpoint = getattr(provider, 'endpoint', None)
        if endpoint is not None:
            parsed = urlsplit(endpoint)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ContextError('review endpoint must not contain credentials or query parameters')
        packet = self.load(record_id)
        if packet['kind'] != 'evaluation':
            raise ContextError('review requires an evaluation record')
        bundle, result = self._materialize(packet)
        limits = limits or ReviewLimits()
        root = Path(__file__).parent
        config = {
            'record_id': record_id, 'reviewed_at': reviewed_at,
            'provider': provider.provider, 'model': provider.model,
            'endpoint': endpoint,
            'timeout_seconds': getattr(provider, 'timeout_seconds', None),
            'limits': asdict(limits), 'prompt_version': PROMPT_VERSION,
            'prompt_sha256': digest(SYSTEM_PROMPT.encode()),
            'system_prompt': SYSTEM_PROMPT, 'response_schema': RESPONSE_SCHEMA,
            'implementation': {name: digest((root / name).read_bytes()) for name in
                               ('engine.py', 'contextual_review.py', 'contextual_provider.py', 'contextual_contracts.py')},
        }
        request_sha = digest(canonical_bytes(config))
        claim, acquired = self.repository.claim_review(request_key, record_id, request_sha)
        if not acquired:
            if claim['state'] == 'finished':
                return self.load(claim['review_record_id'])
            raise ContextError('review_request_in_progress_or_unknown')
        archived = {'provider_request': None, 'provider_response': None,
                    'provider_failure': None}

        def save(value, kind):
            try:
                ref = self._put_json(value, kind)
                self.repository.attach_review_artifact(request_key, request_sha, ref['artifact_id'])
                return ref
            except Exception as exc:
                raise ArchiveError('review artifact persistence failed') from exc

        class RecordingProvider:
            async def review(self, request):
                archived['provider_request'] = save(request, 'engine-review-request')
                try:
                    reply = await provider.review(request)
                except ReviewProviderError as exc:
                    if isinstance(exc.raw, bytes):
                        archived['provider_failure'] = save(
                            {'code': exc.code, 'detail': exc.detail,
                             'raw_utf8': exc.raw.decode('utf-8', 'replace')},
                            'engine-review-provider-failure')
                    raise
                if isinstance(reply, ProviderReply):
                    archived['provider_response'] = save(asdict(reply), 'engine-review-response')
                return reply

        recording = RecordingProvider()
        recording.provider, recording.model = provider.provider, provider.model
        try:
            config_ref = save(config, 'engine-review-config')
            reviewed = await review_evaluation(result, bundle.context, bundle.artifacts, recording,
                                               reviewed_at=reviewed_at, limits=limits,
                                               adapter=DecisionContextV2Adapter())
            review_ref = save(reviewed, 'engine-contextual-review')
            record = self.archive.save_packet({
                'schema_version': 'core-engine-record/1', 'kind': 'review',
                **{key: packet[key] for key in ('input_id', 'batch_id', 'file_id', 'evaluation_id', 'decision')},
                'parent_record_id': record_id, 'request_key': request_key, 'reviewed_at': reviewed_at,
                'request_sha256': request_sha, 'review_config': config_ref,
                'review': review_ref, 'review_schema': save(REVIEW_SCHEMA, 'engine-review-schema'), **archived,
            })
            self.repository.finish_review(request_key, request_sha, record['record_id'])
            return record
        except BaseException:
            self.repository.unknown_review(request_key, request_sha)
            raise
