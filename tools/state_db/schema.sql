-- claude-org state DB schema (Issue #267 / Refs #266)
-- SoT: workers/state-db-hierarchy-design/schema-proposal.md

-- projects ─────────────────────────────────────────────────
CREATE TABLE projects (
  id              INTEGER PRIMARY KEY,
  slug            TEXT NOT NULL UNIQUE,
  display_name    TEXT NOT NULL,
  origin_url      TEXT,
  default_branch  TEXT DEFAULT 'main',
  status          TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','archived','external')),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  archived_at     TEXT,
  notes           TEXT
);
CREATE INDEX idx_projects_status ON projects(status);

-- workstreams ──────────────────────────────────────────────
CREATE TABLE workstreams (
  id              INTEGER PRIMARY KEY,
  project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  slug            TEXT NOT NULL,
  display_name    TEXT NOT NULL,
  epic_issue_url  TEXT,
  status          TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open','closed','archived')),
  opened_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  closed_at       TEXT,
  notes           TEXT,
  UNIQUE (project_id, slug),
  -- Candidate key for the runs(workstream_id, project_id) composite FK (H2).
  UNIQUE (id, project_id)
);
CREATE INDEX idx_ws_status ON workstreams(status);
CREATE INDEX idx_ws_project ON workstreams(project_id);

-- worker_dirs ──────────────────────────────────────────────
-- Defined before runs because runs.worker_dir_id references it.
CREATE TABLE worker_dirs (
  id              INTEGER PRIMARY KEY,
  abs_path        TEXT NOT NULL UNIQUE,
  layout          TEXT NOT NULL CHECK (layout IN ('flat','project','project_workstream')),
  is_git_repo     INTEGER NOT NULL DEFAULT 0,
  is_worktree     INTEGER NOT NULL DEFAULT 0,
  origin_url      TEXT,
  current_branch  TEXT,
  last_seen_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  size_mb         REAL,
  -- lifecycle (H5):
  --   active: in_use/review run, or completed-hot (mtime ≤ 90 days, in _runs/).
  --   scratch: practice dirs; gc candidate after mtime > 90 days.
  --   archived: cold completed runs, read-only references.
  --   delete_pending: physical deletion queued for next curator batch.
  lifecycle       TEXT NOT NULL DEFAULT 'active'
                  CHECK (lifecycle IN ('active','scratch','archived','delete_pending')),
  -- N6: STORED generated column kept in sync with lifecycle (no dual-write).
  archived        INTEGER GENERATED ALWAYS AS
                    (CASE WHEN lifecycle IN ('archived','delete_pending') THEN 1 ELSE 0 END) STORED,
  archive_target  TEXT
);
CREATE INDEX idx_dirs_lifecycle ON worker_dirs(lifecycle);
CREATE INDEX idx_dirs_archived ON worker_dirs(archived);
CREATE INDEX idx_dirs_origin ON worker_dirs(origin_url);

-- runs ─────────────────────────────────────────────────────
CREATE TABLE runs (
  id              INTEGER PRIMARY KEY,
  task_id         TEXT NOT NULL UNIQUE,
  project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
  workstream_id   INTEGER,
  pattern         TEXT NOT NULL CHECK (pattern IN ('A','B','C','D')),
  title           TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'in_use'
                  CHECK (status IN ('queued','in_use','review','completed','failed','suspended','abandoned')),
  branch          TEXT,
  pr_url          TEXT,
  pr_state        TEXT CHECK (pr_state IN ('draft','open','merged','closed') OR pr_state IS NULL),
  issue_refs      TEXT,
  verification    TEXT NOT NULL DEFAULT 'standard'
                  CHECK (verification IN ('minimal','standard','deep')),
  dispatched_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  completed_at    TEXT,
  worker_dir_id   INTEGER REFERENCES worker_dirs(id) ON DELETE SET NULL,
  commit_short    TEXT,
  commit_full     TEXT,
  outcome_note    TEXT,
  -- N1: composite FK with no ON DELETE clause (= NO ACTION). Combined with the
  -- "no physical delete" operating rule, this also blocks the SET NULL × NOT NULL
  -- conflict on project_id. See schema-proposal.md §4.
  FOREIGN KEY (workstream_id, project_id)
    REFERENCES workstreams(id, project_id)
);
CREATE INDEX idx_runs_status ON runs(status);
CREATE INDEX idx_runs_project ON runs(project_id);
CREATE INDEX idx_runs_workstream ON runs(workstream_id);
CREATE INDEX idx_runs_dispatched ON runs(dispatched_at);

-- events (append-only journal) ─────────────────────────────
CREATE TABLE events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  actor           TEXT,
  kind            TEXT NOT NULL,
  run_id          INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  workstream_id   INTEGER REFERENCES workstreams(id) ON DELETE SET NULL,
  project_id      INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  payload_json    TEXT NOT NULL DEFAULT '{}'
                  CHECK (json_valid(payload_json))
);
CREATE INDEX idx_events_occurred ON events(occurred_at);
CREATE INDEX idx_events_kind ON events(kind);
CREATE INDEX idx_events_run ON events(run_id);

-- tags ─────────────────────────────────────────────────────
CREATE TABLE tags (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE
);
CREATE TABLE tag_assignments (
  tag_id          INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  target_type     TEXT NOT NULL CHECK (target_type IN ('project','workstream','run')),
  target_id       INTEGER NOT NULL,
  PRIMARY KEY (tag_id, target_type, target_id)
);
CREATE INDEX idx_tag_target ON tag_assignments(target_type, target_id);

-- unparsed_legacy ──────────────────────────────────────────
-- Lines from legacy markdown / journal that the importer could not parse.
-- Keeps "no row dropped" guarantee while letting humans triage later.
CREATE TABLE unparsed_legacy (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  source          TEXT NOT NULL,
  source_line     INTEGER,
  raw             TEXT NOT NULL,
  reason          TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_unparsed_source ON unparsed_legacy(source);

-- schema_migrations ────────────────────────────────────────
CREATE TABLE schema_migrations (
  version         INTEGER PRIMARY KEY,
  applied_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  description     TEXT NOT NULL
);

INSERT INTO schema_migrations (version, description)
VALUES (1, 'M0: initial schema (Issue #267)');
