create extension if not exists pgcrypto;

create table if not exists tasks (
    id uuid primary key default gen_random_uuid(),
    task_key text not null unique,
    task_name text not null,
    spec_path text not null,
    prompt_path text not null,
    source_type text not null check (source_type in ('builtin', 'custom')),
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists mapping_runs (
    id uuid primary key default gen_random_uuid(),
    task_id uuid references tasks(id) on delete set null,
    task_name_snapshot text not null,
    spec_path_snapshot text not null,
    prompt_path_snapshot text not null,
    student_name text not null,
    input_path text not null,
    output_docx_path text,
    output_xlsx_path text,
    status text not null check (status in ('queued', 'running', 'completed', 'failed')),
    backend text,
    model text,
    error_message text,
    created_at timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists idx_tasks_active on tasks(is_active);
create index if not exists idx_runs_task_id on mapping_runs(task_id);
create index if not exists idx_runs_status on mapping_runs(status);
create index if not exists idx_runs_created_at on mapping_runs(created_at desc);