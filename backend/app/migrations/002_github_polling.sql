-- Snapshots of open issues per project, refreshed by the GitHub polling service.
CREATE TABLE IF NOT EXISTS github_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    issue_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    loom_status TEXT NOT NULL DEFAULT 'unassigned',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, issue_number)
);

-- Snapshots of open (and recently closed) PRs per project.
CREATE TABLE IF NOT EXISTS github_prs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    pr_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    merged INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, pr_number)
);

-- Per-project polling health, written on every poll attempt.
CREATE TABLE IF NOT EXISTS polling_status (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    last_polled_at TIMESTAMP,
    last_ok INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
