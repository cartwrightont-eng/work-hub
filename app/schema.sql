CREATE TABLE IF NOT EXISTS organisations (
  id SERIAL PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  full_name TEXT NOT NULL, email TEXT UNIQUE, password_hash TEXT,
  active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS teams (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  name TEXT NOT NULL, UNIQUE (org_id, name));
CREATE TABLE IF NOT EXISTS team_memberships (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  team_id INT NOT NULL REFERENCES teams(id), user_id INT NOT NULL REFERENCES users(id),
  is_lead BOOLEAN NOT NULL DEFAULT FALSE,
  reports_to INT REFERENCES users(id),
  UNIQUE (team_id, user_id));
CREATE TABLE IF NOT EXISTS roles (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  name TEXT NOT NULL, UNIQUE (org_id, name));
CREATE TABLE IF NOT EXISTS permissions (id SERIAL PRIMARY KEY, key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS role_permissions (
  org_id INT NOT NULL REFERENCES organisations(id),
  role_id INT NOT NULL REFERENCES roles(id),
  permission_id INT NOT NULL REFERENCES permissions(id),
  PRIMARY KEY (role_id, permission_id));
CREATE TABLE IF NOT EXISTS user_roles (
  org_id INT NOT NULL REFERENCES organisations(id),
  user_id INT NOT NULL REFERENCES users(id), role_id INT NOT NULL REFERENCES roles(id),
  PRIMARY KEY (user_id, role_id));
CREATE TABLE IF NOT EXISTS audit_log (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  actor_id INT REFERENCES users(id), action TEXT NOT NULL,
  target_user_id INT REFERENCES users(id), detail JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS organisation_settings (
  org_id INT NOT NULL REFERENCES organisations(id),
  key TEXT NOT NULL, value JSONB NOT NULL, PRIMARY KEY (org_id, key));
CREATE TABLE IF NOT EXISTS deliverables (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  team_id INT NOT NULL REFERENCES teams(id), owner_id INT NOT NULL REFERENCES users(id),
  created_by INT NOT NULL REFERENCES users(id),
  title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  points INT, original_deadline DATE NOT NULL, deadline DATE NOT NULL,
  status TEXT NOT NULL DEFAULT 'backlog'
    CHECK (status IN ('backlog','in_progress','in_review','done','reopened')),
  key_result_id INT,                      -- linked in phase 5
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS status_events (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  deliverable_id INT NOT NULL REFERENCES deliverables(id),
  from_status TEXT, to_status TEXT NOT NULL, actor_id INT NOT NULL REFERENCES users(id),
  note TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS work_logs (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  deliverable_id INT NOT NULL REFERENCES deliverables(id),
  user_id INT NOT NULL REFERENCES users(id), entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
  body TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
-- One row per deliverable with everything the four metrics need, derived only from status_events.
-- Changing a formula means editing this view or app/metrics.py: no data migration.
CREATE OR REPLACE VIEW v_deliverable_facts AS
SELECT d.id, d.org_id, d.owner_id, d.title, d.points, d.status, d.original_deadline, d.deadline,
       x.done_at, (x.done_at AT TIME ZONE x.tz)::date AS done_local_date, x.start_at,
       EXTRACT(EPOCH FROM (x.done_at - x.start_at)) / 86400.0 AS cycle_days, x.reopen_count
FROM deliverables d
CROSS JOIN LATERAL (SELECT
  COALESCE((SELECT value #>> '{}' FROM organisation_settings s WHERE s.org_id=d.org_id AND s.key='timezone'),
           'Africa/Nairobi') AS tz,
  (SELECT max(created_at) FROM status_events e WHERE e.deliverable_id=d.id AND e.to_status='done') AS done_at,
  -- cycle time clock = most recent move into In progress before Done (restarts on reopen)
  (SELECT CASE WHEN COALESCE((SELECT value #>> '{}' FROM organisation_settings s2 WHERE s2.org_id=d.org_id AND s2.key='reopen_rule'),'restart')='first_start'
        THEN min(created_at) ELSE max(created_at) END FROM status_events e WHERE e.deliverable_id=d.id AND e.to_status='in_progress'
     AND e.created_at <= COALESCE((SELECT max(created_at) FROM status_events e2
          WHERE e2.deliverable_id=d.id AND e2.to_status='done'), 'infinity')) AS start_at,
  (SELECT count(*) FROM status_events e WHERE e.deliverable_id=d.id AND e.to_status='reopened') AS reopen_count
) x;
CREATE TABLE IF NOT EXISTS objectives (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  level INT NOT NULL CHECK (level BETWEEN 1 AND 3),          -- 1 company, 2 team, 3 personal
  title TEXT NOT NULL, owner_id INT NOT NULL REFERENCES users(id),
  team_id INT REFERENCES teams(id), parent_id INT REFERENCES objectives(id),
  cycle_start DATE NOT NULL, cycle_end DATE NOT NULL, created_by INT NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS key_results (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  objective_id INT NOT NULL REFERENCES objectives(id), title TEXT NOT NULL,
  owner_id INT NOT NULL REFERENCES users(id),
  start_value NUMERIC NOT NULL DEFAULT 0, target NUMERIC NOT NULL, current NUMERIC NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS kr_updates (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  key_result_id INT NOT NULL REFERENCES key_results(id), actor_id INT NOT NULL REFERENCES users(id),
  old_value NUMERIC, new_value NUMERIC NOT NULL, note TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS documents (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  parent_type TEXT NOT NULL CHECK (parent_type IN ('deliverable','objective','team')),
  parent_id INT NOT NULL, name TEXT NOT NULL, created_by INT NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS document_versions (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  document_id INT NOT NULL REFERENCES documents(id), version INT NOT NULL,
  storage_key TEXT NOT NULL UNIQUE,            -- random name on storage; never the uploaded filename
  original_name TEXT NOT NULL, content_type TEXT NOT NULL, size_bytes INT NOT NULL, sha256 TEXT NOT NULL,
  uploaded_by INT NOT NULL REFERENCES users(id), uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, version));
-- Outbox: notifications are written in the same transaction as the event that causes them, then sent.
-- dedupe_key makes each one fire once; a failed send is retried (up to 5 attempts) without blocking anyone.
CREATE TABLE IF NOT EXISTS notifications (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id), user_id INT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('review','due_soon','overdue')),
  deliverable_id INT REFERENCES deliverables(id), subject TEXT NOT NULL, body TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE, attempts INT NOT NULL DEFAULT 0, error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), sent_at TIMESTAMPTZ);

CREATE TABLE IF NOT EXISTS signup_requests (
  id SERIAL PRIMARY KEY, org_id INT NOT NULL REFERENCES organisations(id),
  full_name TEXT NOT NULL, email TEXT NOT NULL, password_hash TEXT NOT NULL,
  team_id INT NOT NULL REFERENCES teams(id), requested_role TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_by INT REFERENCES users(id), decided_at TIMESTAMPTZ);
