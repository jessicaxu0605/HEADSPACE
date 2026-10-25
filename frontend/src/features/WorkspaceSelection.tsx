import { useState } from "react";
import {
  PageTransition,
  PresenceTransition,
  TextButton,
  motionStaggers,
} from "../component-library";
import type { Workspace } from "../api";
import NewWorkspace from "./NewWorkspace";
import SessionMenu from "./SessionMenu";

type WorkspaceSelectionProps = {
  workspaces: Workspace[];
  onSelect: (workspaceId: string) => void;
  onCreate: (name: string, createSelfNode: boolean) => Promise<void>;
  onLogout: () => void;
};

const lastEditedLabel = (workspace: Workspace): string =>
  new Date(workspace.last_edited_at).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

// Shown when the signed-in user has no current or remembered workspace.
// Choosing one makes it active and opens its landing page; the trailing card
// opens the new-workspace screen in its place.
export default function WorkspaceSelection({
  workspaces,
  onSelect,
  onCreate,
  onLogout,
}: WorkspaceSelectionProps) {
  const [addingWorkspace, setAddingWorkspace] = useState(false);

  return (
    <>
      <SessionMenu
        onLogout={onLogout}
        onShowWorkspaces={
          addingWorkspace ? () => setAddingWorkspace(false) : undefined
        }
      />
      <PageTransition
        className="app-state-transition"
        pageKey={addingWorkspace ? "new-workspace" : "workspace-list"}
      >
        {addingWorkspace ? (
          <NewWorkspace
            onCreate={onCreate}
            onBack={() => setAddingWorkspace(false)}
          />
        ) : (
          <main
            className="workspace-selection"
            aria-labelledby="workspace-selection-title"
          >
            <h1
              id="workspace-selection-title"
              className="workspace-selection-title"
            >
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
                    <span className="workspace-selection-name">
                      {workspace.name}
                    </span>
                    <span className="workspace-selection-meta">
                      <span>last edited {lastEditedLabel(workspace)}</span>
                      <span>{workspace.role}</span>
                    </span>
                  </button>
                </PresenceTransition>
              ))}
              <PresenceTransition
                as="li"
                enter="fade-up"
                enterDelay={
                  Math.min(workspaces.length, 5) * motionStaggers.standard
                }
                visible
              >
                <div className="workspace-selection-card workspace-selection-new">
                  <TextButton onClick={() => setAddingWorkspace(true)}>
                    + new space
                  </TextButton>
                </div>
              </PresenceTransition>
            </ul>
          </main>
        )}
      </PageTransition>
    </>
  );
}
