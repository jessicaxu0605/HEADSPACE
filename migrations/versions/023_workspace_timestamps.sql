-- workspaces.updated_at tracks changes to the workspace row itself (such as a
-- rename), like the other tables using set_updated_at().
DROP TRIGGER IF EXISTS workspaces_set_updated_at ON workspaces;
CREATE TRIGGER workspaces_set_updated_at
BEFORE UPDATE ON workspaces
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- A workspace's "last edited" time is its newest node or edge change, read at
-- listing time; these make each lookup a single index probe.
CREATE INDEX IF NOT EXISTS memory_node_workspace_updated_idx
  ON memory_node (workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS memory_edge_workspace_updated_idx
  ON memory_edge (workspace_id, updated_at DESC);
