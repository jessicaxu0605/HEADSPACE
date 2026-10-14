-- Provision the workspace used by requests that have no authenticated workspace
-- context in development. Runtime requests only reference these stable IDs; they
-- do not create or update workspace records.

INSERT INTO users (id, email, display_name)
VALUES (
  '00000000-0000-0000-0000-000000000001',
  'development@headspace.invalid',
  'Development User'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO workspaces (id, name, created_by_user_id)
VALUES (
  '00000000-0000-0000-0000-000000000002',
  'Headspace Development',
  '00000000-0000-0000-0000-000000000001'
)
ON CONFLICT (id) DO NOTHING;
