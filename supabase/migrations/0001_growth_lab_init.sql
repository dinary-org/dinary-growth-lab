-- DINARY Growth Lab research store.
-- Lives in its own schema so it never touches the app's `public` tables.
-- After applying: Project Settings → API → "Exposed schemas" → add `growth_lab`
-- so PostgREST (used by dgl.sinks.SupabaseSink with the service-role key) can reach it.

create schema if not exists growth_lab;

create table if not exists growth_lab.videos (
  platform text not null, video_id text not null, url text not null,
  creator_id text, creator_name text, published_at timestamptz, title text, description text,
  hashtags jsonb, tags jsonb, duration_s real, language text, discovered_via text,
  first_seen_at timestamptz, raw jsonb,
  primary key (platform, video_id));

create table if not exists growth_lab.video_snapshots (
  platform text not null, video_id text not null, observed_at timestamptz not null,
  views bigint, likes bigint, comments bigint, shares bigint, saves bigint, source text, missing jsonb,
  primary key (platform, video_id, observed_at));  -- no FK: sync batches may land out of order
create index if not exists video_snapshots_time on growth_lab.video_snapshots (observed_at);

create table if not exists growth_lab.creators (
  platform text not null, creator_id text not null, name text, followers bigint, total_views bigint,
  video_count bigint, baseline_median_views real, baseline_n int, observed_at timestamptz, raw jsonb,
  primary key (platform, creator_id));

create table if not exists growth_lab.analyses (
  analysis_id text primary key, platform text, video_id text,
  access_level text check (access_level in ('metadata_only', 'visual', 'full_av')),
  media_source text, rights_basis text not null, created_at timestamptz, features jsonb, observations jsonb);
create index if not exists analyses_video on growth_lab.analyses (platform, video_id);

create table if not exists growth_lab.cluster_runs (
  run_id text primary key, created_at timestamptz, space text check (space in ('topic', 'format')),
  params jsonb, summary jsonb);

create table if not exists growth_lab.clusters (
  run_id text references growth_lab.cluster_runs on delete cascade, cluster_id text, space text, label text,
  explanation jsonb, size int, n_creators int, momentum real, lineage jsonb, stats jsonb,
  primary key (run_id, cluster_id));

create table if not exists growth_lab.cluster_members (
  run_id text, cluster_id text, platform text, video_id text, weight real,
  primary key (run_id, cluster_id, platform, video_id));

create table if not exists growth_lab.opportunities (
  opportunity_id text primary key, run_id text, created_at timestamptz, topic_cluster text, format_cluster text,
  score real, score_low real, score_high real, is_exploration boolean, components jsonb, evidence jsonb);

create table if not exists growth_lab.concepts (
  concept_id text primary key, opportunity_id text, created_at timestamptz,
  status text check (status in ('proposed', 'approved_for_production', 'rejected', 'produced')),
  title_ko text, payload jsonb);

create table if not exists growth_lab.productions (
  production_id text primary key, concept_id text, created_at timestamptz,
  status text check (status in ('rendering', 'qc_failed', 'awaiting_approval', 'approved', 'rejected', 'published')),
  video_path text, storage_url text, qc jsonb, approved_by text, approved_at timestamptz);

create table if not exists growth_lab.own_posts (
  post_id text primary key, production_id text, concept_id text, platform text, platform_video_id text,
  url text, published_at timestamptz, hypothesis jsonb);

create table if not exists growth_lab.own_snapshots (
  post_id text references growth_lab.own_posts on delete cascade, observed_at timestamptz,
  views bigint, reach bigint, likes bigint, comments bigint, shares bigint, saves bigint,
  avg_view_pct real, follows bigint, raw jsonb, primary key (post_id, observed_at));

create table if not exists growth_lab.integration_checks (
  check_id text primary key, integration text, status text, checked_at timestamptz, detail text);

create table if not exists growth_lab.job_runs (
  job_id text primary key, job text, started_at timestamptz, finished_at timestamptz, status text,
  attempts int, cost jsonb, log text);

-- Research data is server-side only: RLS on, no policies → anon/authenticated roles get nothing;
-- the service role (used by the pipeline) bypasses RLS.
do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'growth_lab' loop
    execute format('alter table growth_lab.%I enable row level security', t);
  end loop;
end $$;

grant usage on schema growth_lab to service_role;
grant all on all tables in schema growth_lab to service_role;
alter default privileges in schema growth_lab grant all on tables to service_role;
