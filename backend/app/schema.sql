-- Everything the desk remembers. The event log is the source of truth for the feed,
-- run traces, mood and "how often have I asked" counters.

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,          -- ISO 8601, UTC
    agent     TEXT    NOT NULL,          -- 'patch', 'pitch', 'desk'
    type      TEXT    NOT NULL,          -- run.started, run.step, finding, ...
    run_id    TEXT,                      -- groups the steps of one run
    repo      TEXT,
    payload   TEXT    NOT NULL           -- JSON
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS events_repo ON events(repo);
CREATE INDEX IF NOT EXISTS events_type ON events(type);

-- Conditional-request cache: a 304 from GitHub means "use what you have".
CREATE TABLE IF NOT EXISTS http_cache (
    key        TEXT PRIMARY KEY,         -- identity + URL
    etag       TEXT NOT NULL,
    body       TEXT NOT NULL,            -- JSON: {"data": ..., "next": url or null}
    fetched_at TEXT NOT NULL
);

-- Latest audit of each repository.
CREATE TABLE IF NOT EXISTS repos (
    full_name  TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,            -- project, profile, placeholder, skipped
    snapshot   TEXT NOT NULL,            -- JSON RepoSnapshot
    meta_fp    TEXT NOT NULL,            -- fingerprint of the metadata the checks read
    pushed_at  TEXT,
    score      INTEGER,                  -- NULL when the repository is not scored
    findings   TEXT NOT NULL,            -- JSON list of Finding
    synced_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_scores (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT    NOT NULL,
    ts        TEXT    NOT NULL,
    score     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS repo_scores_name ON repo_scores(full_name, id);

-- Changes an agent wants to make. Nothing is applied until you approve it.
CREATE TABLE IF NOT EXISTS proposals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent      TEXT NOT NULL,
    kind       TEXT NOT NULL,            -- metadata_sweep, pull_request
    repo       TEXT,                     -- NULL when it spans repositories
    title      TEXT NOT NULL,
    summary    TEXT NOT NULL,
    status     TEXT NOT NULL,            -- pending, approved, rejected, applied, failed, superseded
    payload    TEXT NOT NULL,            -- JSON: exactly what would be applied
    draft_key  TEXT NOT NULL,            -- fingerprint of the input, so the same draft isn't made twice
    decision   TEXT,                     -- JSON: what you decided, and why
    result     TEXT,                     -- JSON: what happened when it was applied
    run_id     TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS proposals_status ON proposals(status);
CREATE INDEX IF NOT EXISTS proposals_key ON proposals(agent, draft_key);

-- Daily totals for the metrics page: requests, model calls, tokens, quiet checks.
CREATE TABLE IF NOT EXISTS daily_stats (
    day   TEXT NOT NULL,                 -- YYYY-MM-DD, local time
    agent TEXT NOT NULL,
    key   TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (day, agent, key)
);

-- What an agent has learned from your decisions. Active lessons join its system prompt.
CREATE TABLE IF NOT EXISTS lessons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent       TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    source      TEXT    NOT NULL,        -- rejection, edit, you
    proposal_id INTEGER,                 -- the decision it came from, if any
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL
);

-- Plan usage as Claude Code reported it, so the metrics page can draw it against the stop.
CREATE TABLE IF NOT EXISTS usage_readings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    scope   TEXT NOT NULL,               -- which limit: session (5-hour) or weekly
    percent REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
