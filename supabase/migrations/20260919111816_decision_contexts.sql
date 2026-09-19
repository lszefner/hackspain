create table ingestion.decision_contexts (
  context_id text primary key check (context_id ~ '^dc_[a-f0-9]{64}$'),
  file_id text not null check (length(file_id) > 0),
  context_artifact_id uuid not null references ingestion.artifacts(id) on delete restrict,
  schema_artifact_id uuid not null references ingestion.artifacts(id) on delete restrict,
  evaluation_artifact_id uuid not null references ingestion.artifacts(id) on delete restrict,
  context_sha256 text not null check (context_sha256 ~ '^[a-f0-9]{64}$'),
  schema_sha256 text not null check (schema_sha256 ~ '^[a-f0-9]{64}$'),
  evaluation_date date not null,
  data_status text not null check (data_status in ('ready', 'blocked')),
  execution_status text not null check (execution_status in ('supported', 'blocked')),
  decision text not null check (decision in ('PAGAR', 'ESCALAR', 'NO_PAGAR')),
  created_at timestamptz not null default now()
);
create index decision_contexts_file_created_idx on ingestion.decision_contexts(file_id, created_at desc);
create index decision_contexts_context_artifact_idx on ingestion.decision_contexts(context_artifact_id);
create index decision_contexts_schema_artifact_idx on ingestion.decision_contexts(schema_artifact_id);
create index decision_contexts_evaluation_artifact_idx on ingestion.decision_contexts(evaluation_artifact_id);
alter table ingestion.decision_contexts enable row level security;
revoke all on ingestion.decision_contexts from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on ingestion.decision_contexts from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on ingestion.decision_contexts from authenticated;
  end if;
end
$$;
