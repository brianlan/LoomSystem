CREATE TABLE IF NOT EXISTS github_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    issue_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    is_open INTEGER NOT NULL DEFAULT 0,
    assignees_json TEXT NOT NULL DEFAULT '[]',
    labels_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}',
    loom_status TEXT NOT NULL DEFAULT 'unassigned',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, issue_number)
);

CREATE TABLE IF NOT EXISTS github_pull_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    pr_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    is_open INTEGER NOT NULL DEFAULT 0,
    is_draft INTEGER NOT NULL DEFAULT 0,
    is_merged INTEGER NOT NULL DEFAULT 0,
    merged_at TIMESTAMP,
    raw_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, pr_number)
);

CREATE TABLE IF NOT EXISTS github_polling_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    last_success_at TIMESTAMP,
    last_error_at TIMESTAMP,
    last_error_message TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
