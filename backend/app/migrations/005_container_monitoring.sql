-- Container monitoring and restart recovery (T13).
ALTER TABLE agent_instances ADD COLUMN restart_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_instances ADD COLUMN last_restart_at TIMESTAMP;
