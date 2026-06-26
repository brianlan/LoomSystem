-- Add container_name for deterministic container lookup (restart recovery, T13).
ALTER TABLE agent_instances ADD COLUMN container_name TEXT;
