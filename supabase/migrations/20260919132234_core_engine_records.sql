create table ingestion.engine_runs (
  request_key text primary key check (length(request_key) between 1 and 200),
  request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
  state text not null default 'running' check (state in ('running','completed','partial','failed','unknown')),
  batch_id uuid unique references ingestion.batches(id) on delete restrict,
  input_artifact_id uuid references ingestion.artifacts(id) on delete restrict,
  result_artifact_id uuid references ingestion.artifacts(id) on delete restrict,
  error_code text,
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  check (state not in ('completed','partial','failed') or result_artifact_id is not null)
);
create index engine_runs_input_artifact_idx on ingestion.engine_runs(input_artifact_id);
create index engine_runs_result_artifact_idx on ingestion.engine_runs(result_artifact_id);
alter table ingestion.engine_runs enable row level security;
revoke all on ingestion.engine_runs from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then revoke all on ingestion.engine_runs from anon; end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then revoke all on ingestion.engine_runs from authenticated; end if;
end
$$;

create table ingestion.engine_records (
  record_id text primary key check (record_id ~ '^er_[a-f0-9]{64}$'),
  kind text not null check (kind in ('evaluation', 'review')),
  input_id uuid not null references ingestion.inputs(id) on delete restrict,
  parent_record_id text references ingestion.engine_records(record_id) on delete restrict,
  artifact_id uuid not null references ingestion.artifacts(id) on delete restrict,
  evaluation_id text not null check (evaluation_id ~ '^ev_[a-f0-9]{64}$'),
  decision text not null check (decision in ('PAGAR', 'ESCALAR', 'NO_PAGAR')),
  created_at timestamptz not null default now(),
  check ((kind = 'evaluation' and parent_record_id is null) or (kind = 'review' and parent_record_id is not null))
);
create index engine_records_input_created_idx on ingestion.engine_records(input_id, created_at desc);
create index engine_records_parent_idx on ingestion.engine_records(parent_record_id);
create index engine_records_artifact_idx on ingestion.engine_records(artifact_id);
create index engine_records_evaluation_idx on ingestion.engine_records(evaluation_id);
create table ingestion.engine_review_requests (
  request_key text primary key check (length(request_key) between 1 and 200),
  evaluation_record_id text not null references ingestion.engine_records(record_id) on delete restrict,
  request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
  state text not null default 'running' check (state in ('running', 'finished', 'unknown')),
  review_record_id text references ingestion.engine_records(record_id) on delete restrict,
  error_code text,
  artifact_ids uuid[] not null default '{}'::uuid[],
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  check ((state = 'finished' and review_record_id is not null) or (state <> 'finished' and review_record_id is null))
);
create index engine_review_requests_evaluation_idx on ingestion.engine_review_requests(evaluation_record_id);
create index engine_review_requests_review_idx on ingestion.engine_review_requests(review_record_id);
alter table ingestion.engine_records enable row level security;
alter table ingestion.engine_review_requests enable row level security;
revoke all on ingestion.engine_records, ingestion.engine_review_requests from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on ingestion.engine_records, ingestion.engine_review_requests from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on ingestion.engine_records, ingestion.engine_review_requests from authenticated;
  end if;
end
$$;
