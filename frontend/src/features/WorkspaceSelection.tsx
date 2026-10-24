import { PresenceTransition, motionStaggers } from "../component-library";
import type { Workspace } from "../api";
import SessionMenu from "./SessionMenu";

type WorkspaceSelectionProps = {
  workspaces: Workspace[];
  onSelect: (workspaceId: string) => void;
  onLogout: () => void;
};

const lastEditedLabel = (workspace: Workspace): string =>
  new Date(workspace.last_edited_at).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

// Shown when the signed-in user has no current or remembered workspace.
// Choosing one makes it active and opens its landing page.
export default function WorkspaceSelection({
  workspaces,
  onSelect,
  onLogout,
}: WorkspaceSelectionProps) {
  return (
    <main className="workspace-selection" aria-labelledby="workspace-selection-title">
      <SessionMenu onLogout={onLogout} />
      <h1 id="workspace-selection-title" className="workspace-selection-title">
        what HEADSPACE are you in today?
      </h1>
      <ul className="workspace-selection-list">
        {workspaces.map((workspace, index) => (
          <PresenceTransition
            as="li"
            key={workspace.id}
            enter="fade-up"
            enterDelay={Math.min(index, 5) * motionStaggers.standard}
            visible
          >
            <button
              className="workspace-selection-card"
              type="button"
              onClick={() => onSelect(workspace.id)}
            >
              <span className="workspace-selection-name">{workspace.name}</span>
              <span className="workspace-selection-meta">
                <span>last edited {lastEditedLabel(workspace)}</span>
                <span>{workspace.role}</span>
              </span>
            </button>
          </PresenceTransition>
        ))}
      </ul>
    </main>
  );
}
