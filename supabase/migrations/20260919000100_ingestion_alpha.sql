-- Private persistence for the invoice ingestion worker.
-- The schema is deliberately outside Supabase's exposed `public` schema.
create extension if not exists pgcrypto;

create schema if not exists ingestion;
revoke all on schema ingestion from public;

-- Supabase's storage service owns this table.  The guard keeps the migration
-- runnable against a plain Postgres used by local repository tests.
do $$
begin
  if to_regclass('storage.buckets') is not null then
    execute $bucket$insert into storage.buckets (id, name, public)
      values ('invoice-ingestion-private', 'invoice-ingestion-private', false)
      on conflict (id) do nothing$bucket$;
  end if;
end
$$;

create table if not exists ingestion.batches (
  id uuid primary key default gen_random_uuid(),
  manifest jsonb not null,
  manifest_hash text not null,
  config jsonb not null default '{}'::jsonb,
  config_hash text not null,
  status text not null default 'pending' check (status in ('pending', 'running', 'partial', 'completed', 'succeeded', 'failed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists ingestion.inputs (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null references ingestion.batches(id) on delete cascade,
  relative_path text not null,
  file_name text not null,
  content_hash text,
  object_key text,
  size_bytes bigint,
  created_at timestamptz not null default now(),
  unique (batch_id, relative_path)
);

create table if not exists ingestion.jobs (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null references ingestion.batches(id) on delete cascade,
  input_id uuid not null references ingestion.inputs(id) on delete cascade,
  stage text not null check (stage in ('original', 'render', 'reading', 'interpretation')),
  input_artifact_hash text,
  reading_hash text,
  provider text not null,
  model text not null,
  provider_revision text,
  config_version text,
  prompt_version text,
  adapter_version text,
  schema_hash text,
  settings jsonb not null default '{}'::jsonb,
  work_key text not null unique,
  state text not null default 'pending' check (state in ('pending', 'running', 'retry_wait', 'succeeded', 'needs_review', 'failed', 'unknown')),
  max_attempts integer not null default 3 check (max_attempts > 0),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  lease_owner text,
  lease_token uuid,
  lease_expires_at timestamptz,
  next_attempt_at timestamptz not null default now(),
  artifact_id uuid,
  last_error jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists ingestion.attempts (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references ingestion.jobs(id) on delete cascade,
  attempt_number integer not null check (attempt_number > 0),
  provider_request_id text,
  status text not null default 'running' check (status in ('running', 'succeeded', 'needs_review', 'failed', 'unknown')),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  error jsonb,
  usage jsonb,
  raw_artifact_ids jsonb not null default '[]'::jsonb,
  latency_seconds numeric,
  request_metadata jsonb,
  unique (job_id, attempt_number)
);

create table if not exists ingestion.artifacts (
  id uuid primary key default gen_random_uuid(),
  sha256 text not null,
  kind text not null,
  object_key text not null,
  content_type text not null,
  byte_size bigint not null check (byte_size >= 0),
  payload jsonb,
  parent_artifact_ids uuid[] not null default '{}'::uuid[],
  verified_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (sha256, kind)
);

alter table ingestion.jobs
  add constraint jobs_artifact_id_fkey foreign key (artifact_id)
  references ingestion.artifacts(id) on delete restrict;

create table if not exists ingestion.input_results (
  id uuid primary key default gen_random_uuid(),
  input_id uuid not null references ingestion.inputs(id) on delete cascade,
  interpreter text not null,
  job_id uuid references ingestion.jobs(id) on delete restrict,
  artifact_id uuid references ingestion.artifacts(id) on delete restrict,
  status text not null check (status in ('completed', 'needs_review', 'failed', 'unknown')),
  error jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (input_id, interpreter)
);

create index if not exists inputs_batch_id_idx on ingestion.inputs(batch_id);
create index if not exists jobs_batch_id_idx on ingestion.jobs(batch_id);
create index if not exists jobs_input_id_idx on ingestion.jobs(input_id);
create index if not exists jobs_artifact_id_idx on ingestion.jobs(artifact_id);
create index if not exists jobs_claim_idx on ingestion.jobs(state, next_attempt_at, lease_expires_at);
create index if not exists attempts_job_id_idx on ingestion.attempts(job_id);
create index if not exists artifacts_parent_ids_idx on ingestion.artifacts using gin(parent_artifact_ids);
create index if not exists input_results_input_id_idx on ingestion.input_results(input_id);
create index if not exists input_results_job_id_idx on ingestion.input_results(job_id);
create index if not exists input_results_artifact_id_idx on ingestion.input_results(artifact_id);

-- There is no frontend access path for this schema.  Backend roles should be
-- granted explicitly by deployment configuration rather than granting PUBLIC.
revoke all on all tables in schema ingestion from public;
revoke all on all sequences in schema ingestion from public;
