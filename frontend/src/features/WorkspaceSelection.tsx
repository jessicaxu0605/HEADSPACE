import { PresenceTransition, TextButton, motionStaggers } from "../component-library";
import type { Workspace } from "../api";

type WorkspaceSelectionProps = {
  workspaces: Workspace[];
  onSelect: (workspaceId: string) => void;
  onLogout: () => void;
};

const createdLabel = (workspace: Workspace): string =>
  new Date(workspace.created_at).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
  });

// Shown when the signed-in user has no current or remembered workspace. Choosing one makes it active and opens its landing page.
export default function WorkspaceSelection({
  workspaces,
  onSelect,
  onLogout,
}: WorkspaceSelectionProps) {
  return (
    <main className="auth-shell">
      <section
        className="auth-card auth-welcome workspace-selection"
        aria-labelledby="workspace-selection-title"
      >
        <h1 id="workspace-selection-title">Choose a headspace</h1>
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
                className="workspace-selection-button"
                type="button"
                onClick={() => onSelect(workspace.id)}
              >
                <span className="workspace-selection-name">{workspace.name}</span>
                <span className="workspace-selection-meta">
                  {workspace.role} · created {createdLabel(workspace)}
                </span>
              </button>
            </PresenceTransition>
          ))}
        </ul>
        <div className="auth-entry-actions">
          <TextButton className="auth-entry-action" onClick={onLogout}>
            log out
          </TextButton>
        </div>
      </section>
    </main>
  );
}
