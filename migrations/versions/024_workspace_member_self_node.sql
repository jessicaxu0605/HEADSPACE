-- A member's own person node in a workspace is recorded on their membership
-- rather than as a `headspace_user` external ID: the account UUID is not an
-- identity the user would recognize, and the node stays a plain person. NULL
-- when the member has no self node. The composite key keeps the node in the
-- membership's workspace; nodes are soft-deleted, so no ON DELETE action.

ALTER TABLE workspace_member ADD COLUMN IF NOT EXISTS self_node_id uuid;

ALTER TABLE workspace_member
  ADD CONSTRAINT workspace_member_self_node_fkey
  FOREIGN KEY (workspace_id, self_node_id) REFERENCES memory_node(workspace_id, id);

-- One node cannot be the self node of two members.
ALTER TABLE workspace_member
  ADD CONSTRAINT workspace_member_self_node_unique UNIQUE (workspace_id, self_node_id);

UPDATE workspace_member AS wm
SET self_node_id = x.node_id
FROM node_external_id AS x
WHERE x.provider = 'headspace_user'
  AND x.workspace_id = wm.workspace_id
  AND x.external_id = wm.user_id::text;

-- The identity projection lists external-ID providers, so rebuild those nodes.
INSERT INTO projection_rebuild_queue (workspace_id, object_type, object_id)
SELECT DISTINCT workspace_id, 'node', node_id
FROM node_external_id
WHERE provider = 'headspace_user'
ON CONFLICT DO NOTHING;

DELETE FROM node_external_id WHERE provider = 'headspace_user';
