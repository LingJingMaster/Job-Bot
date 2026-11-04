-- Job-Bot SQLite schema. 连接时还必须执行 PRAGMA foreign_keys=ON 与 journal_mode=WAL。
-- 本脚本可重复执行：表、索引、触发器均使用 IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS schema_migration (
    id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('resume', 'jd_paste', 'jd_import')),
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    mime TEXT,
    size INTEGER,
    text TEXT CHECK (text IS NULL OR length(text) <= 100000),
    parse_status TEXT NOT NULL CHECK (parse_status IN ('pending', 'ok', 'failed')),
    parse_error TEXT
);

CREATE TABLE IF NOT EXISTS profile (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    name TEXT,
    current_revision INTEGER
);

CREATE TABLE IF NOT EXISTS fact (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    profile_id TEXT NOT NULL REFERENCES profile(id),
    fact_key TEXT NOT NULL,
    revision INTEGER NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('education', 'work', 'project', 'skill', 'award', 'certificate', 'other')),
    statement TEXT NOT NULL,
    structured TEXT,
    source_ref TEXT,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'retracted')),
    contribution TEXT CHECK (contribution IS NULL OR contribution IN ('support', 'participate', 'own', 'push', 'lead')),
    is_estimate INTEGER NOT NULL DEFAULT 0 CHECK (is_estimate IN (0, 1)),
    estimate_note TEXT,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS profile_snapshot (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    profile_id TEXT NOT NULL REFERENCES profile(id),
    revision INTEGER NOT NULL,
    fact_ids TEXT NOT NULL,
    sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preference (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    profile_id TEXT NOT NULL REFERENCES profile(id),
    revision INTEGER NOT NULL,
    hard TEXT,
    soft TEXT,
    search_terms TEXT
);

CREATE TABLE IF NOT EXISTS collect_run (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('assist', 'auto', 'manual')),
    keyword TEXT,
    city TEXT,
    params TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'finished', 'stopped', 'failed')),
    stop_reason TEXT,
    started_at TEXT,
    finished_at TEXT,
    n_seen INTEGER NOT NULL DEFAULT 0,
    n_ok INTEGER NOT NULL DEFAULT 0,
    n_fail INTEGER NOT NULL DEFAULT 0,
    collector_version TEXT
);

CREATE TABLE IF NOT EXISTS collect_command (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES collect_run(id),
    command TEXT NOT NULL CHECK (command IN ('start', 'stop')),
    payload TEXT,
    handled_at TEXT
);

CREATE TABLE IF NOT EXISTS job (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('boss', 'manual')),
    external_id TEXT,
    url TEXT,
    dedup_group TEXT,
    latest_snapshot_id TEXT,
    FOREIGN KEY (latest_snapshot_id) REFERENCES job_snapshot(id)
);

CREATE TABLE IF NOT EXISTS llm_call (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    purpose TEXT NOT NULL,
    model TEXT,
    template_hash TEXT,
    input_ref TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    latency_ms INTEGER,
    cost REAL,
    cost_kind TEXT CHECK (cost_kind IS NULL OR cost_kind IN ('actual', 'estimated', 'unavailable')),
    status TEXT NOT NULL,
    cache_hit INTEGER NOT NULL DEFAULT 0 CHECK (cache_hit IN (0, 1)),
    request_id TEXT
);

CREATE TABLE IF NOT EXISTS job_snapshot (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES job(id),
    run_id TEXT REFERENCES collect_run(id),
    fetched_at TEXT NOT NULL,
    raw_path TEXT,
    content_hash TEXT NOT NULL,
    title TEXT,
    company TEXT,
    city TEXT,
    district TEXT,
    salary_text TEXT,
    salary_min INTEGER CHECK (salary_min IS NULL OR salary_min != 0),
    salary_max INTEGER CHECK (salary_max IS NULL OR salary_max != 0),
    salary_unit TEXT CHECK (salary_unit IS NULL OR salary_unit IN ('month_k', 'day_yuan', 'hour_yuan', 'unknown')),
    salary_months INTEGER CHECK (salary_months IS NULL OR salary_months != 0),
    experience_text TEXT,
    degree_text TEXT,
    description TEXT NOT NULL CHECK (length(description) <= 100000),
    hr_title TEXT,
    hr_active_text TEXT,
    availability TEXT NOT NULL CHECK (availability IN ('active', 'closed', 'unknown')),
    source TEXT NOT NULL CHECK (source IN ('boss', 'manual')),
    UNIQUE (job_id, content_hash)
);

CREATE TABLE IF NOT EXISTS jd_parse (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES job_snapshot(id),
    llm_call_id TEXT REFERENCES llm_call(id),
    status TEXT NOT NULL CHECK (status IN ('ok', 'needs_manual', 'failed')),
    requirements TEXT
);

CREATE TABLE IF NOT EXISTS match_run (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    profile_snapshot_id TEXT NOT NULL REFERENCES profile_snapshot(id),
    preference_id TEXT NOT NULL REFERENCES preference(id),
    method TEXT NOT NULL CHECK (method IN ('b0', 'b1', 'b2', 'b3', 'llm')),
    config TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    n_jobs INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS match_result (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES match_run(id),
    snapshot_id TEXT NOT NULL REFERENCES job_snapshot(id),
    hard_result TEXT NOT NULL CHECK (hard_result IN ('pass', 'fail', 'needs_review')),
    hard_reasons TEXT,
    score REAL,
    rank INTEGER,
    components TEXT,
    evidence TEXT,
    unknown_fields TEXT
);

CREATE TABLE IF NOT EXISTS label (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    profile_id TEXT NOT NULL REFERENCES profile(id),
    snapshot_id TEXT NOT NULL REFERENCES job_snapshot(id),
    rating INTEGER CHECK (rating IS NULL OR (rating >= 0 AND rating <= 3)),
    hard_label TEXT CHECK (hard_label IS NULL OR hard_label IN ('pass', 'fail', 'unclear')),
    labeler TEXT NOT NULL,
    labeled_at TEXT,
    note TEXT,
    round TEXT NOT NULL CHECK (round IN ('first', 'recheck', 'second')),
    UNIQUE (profile_id, snapshot_id, labeler, round)
);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES job_snapshot(id),
    kind TEXT NOT NULL CHECK (kind IN ('save', 'reject')),
    reason TEXT NOT NULL CHECK (reason IN ('location', 'pay', 'duty', 'company', 'experience', 'duplicate', 'other'))
);

CREATE TABLE IF NOT EXISTS material (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    profile_snapshot_id TEXT NOT NULL REFERENCES profile_snapshot(id),
    snapshot_id TEXT NOT NULL REFERENCES job_snapshot(id),
    kind TEXT NOT NULL CHECK (kind IN ('resume', 'ats_text', 'opening_msg')),
    mode TEXT NOT NULL CHECK (mode IN ('pipeline', 'polish', 'raw') OR mode LIKE 'ablation_%'),
    revision INTEGER NOT NULL,
    content TEXT NOT NULL CHECK (length(content) <= 100000),
    status TEXT NOT NULL CHECK (status IN ('draft', 'needs_input', 'verified', 'approved', 'superseded')),
    content_hash TEXT NOT NULL,
    parent_id TEXT REFERENCES material(id)
);

CREATE TABLE IF NOT EXISTS claim (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    material_id TEXT NOT NULL REFERENCES material(id),
    span_start INTEGER NOT NULL,
    span_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('factual', 'non_factual')),
    fact_ids TEXT,
    requirement_ids TEXT,
    check_status TEXT NOT NULL CHECK (check_status IN ('supported', 'unsupported', 'needs_review')),
    check_detail TEXT
);

CREATE TABLE IF NOT EXISTS question (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    material_id TEXT NOT NULL REFERENCES material(id),
    requirement_id TEXT NOT NULL,
    text TEXT NOT NULL,
    answer TEXT,
    answered_at TEXT,
    resulting_fact_id TEXT REFERENCES fact(id)
);

CREATE TABLE IF NOT EXISTS application (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE REFERENCES job(id),
    snapshot_id TEXT NOT NULL REFERENCES job_snapshot(id),
    stage TEXT NOT NULL CHECK (stage IN (
        'interested', 'preparing', 'ready', 'applied',
        'communicating', 'interviewing', 'offer', 'closed'
    )),
    material_id TEXT REFERENCES material(id),
    note TEXT,
    closed_reason TEXT
);

CREATE TABLE IF NOT EXISTS application_event (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage TEXT,
    to_stage TEXT NOT NULL,
    reason TEXT,
    origin TEXT NOT NULL CHECK (origin = 'manual'),
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_run (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    experiment TEXT NOT NULL,
    config TEXT NOT NULL,
    git_commit TEXT,
    started_at TEXT,
    finished_at TEXT,
    results_path TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_job_platform_external_id
    ON job(platform, external_id);

CREATE INDEX IF NOT EXISTS idx_job_snapshot_job_id_fetched_at
    ON job_snapshot(job_id, fetched_at);

CREATE INDEX IF NOT EXISTS idx_fact_profile_id_status
    ON fact(profile_id, status);

CREATE INDEX IF NOT EXISTS idx_match_result_run_id_rank
    ON match_result(run_id, rank);

CREATE INDEX IF NOT EXISTS idx_label_profile_id
    ON label(profile_id);

CREATE INDEX IF NOT EXISTS idx_llm_call_purpose_created_at
    ON llm_call(purpose, created_at);

CREATE TRIGGER IF NOT EXISTS job_snapshot_content_immutable
BEFORE UPDATE OF
    job_id, run_id, fetched_at, raw_path, content_hash, title, company, city, district,
    salary_text, salary_min, salary_max, salary_unit, salary_months, experience_text,
    degree_text, description, hr_title, hr_active_text, availability, source
ON job_snapshot
BEGIN
    SELECT RAISE(ABORT, 'job_snapshot 正文字段不可更新');
END;

CREATE TRIGGER IF NOT EXISTS material_approved_content_immutable
BEFORE UPDATE OF content, content_hash ON material
FOR EACH ROW
WHEN OLD.status = 'approved'
BEGIN
    SELECT RAISE(ABORT, 'material status=approved 后不可修改 content');
END;
