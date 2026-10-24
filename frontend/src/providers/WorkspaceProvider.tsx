import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  listWorkspaces,
  markWorkspaceLastUsed,
  subscribeWorkspaceAccessDenied,
  type Workspace,
  type WorkspaceRole,
} from "../api";
import { useSession } from "./SessionProvider";

export type ActiveWorkspace = {
  workspace: Workspace;
  workspaces: Workspace[];
  role: WorkspaceRole;
  canWrite: boolean;
  switchWorkspace: (workspaceId: string) => void;
  showAllWorkspaces: () => void;
};

export type WorkspaceState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string; retry: () => void }
  | { status: "empty" }
  | {
      status: "selecting";
      workspaces: Workspace[];
      switchWorkspace: (workspaceId: string) => void;
    }
  | ({ status: "ready" } & ActiveWorkspace);

type LoadedWorkspaces = {
  userId: string;
  workspaces: Workspace[];
  activeId: string | null;
};

const WorkspaceContext = createContext<WorkspaceState | null>(null);
const ActiveWorkspaceContext = createContext<ActiveWorkspace | null>(null);

// Keep the current choice if it is still available, then the one the server
// remembers as last used (signup marks the default workspace, so a new account
// opens straight into it). Otherwise null asks the user to pick.
const chooseWorkspace = (
  workspaces: Workspace[],
  currentId: string | null,
  lastUsedId: string | null,
): string | null => {
  const available = new Set(workspaces.map((workspace) => workspace.id));
  return (
    [currentId, lastUsedId].find((id) => id !== null && available.has(id)) ??
    null
  );
};

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const session = useSession();
  const userId = session.status === "signed-in" ? session.user.id : null;
  const [loaded, setLoaded] = useState<LoadedWorkspaces | null>(null);
  const [failure, setFailure] = useState<{ userId: string; message: string } | null>(
    null,
  );
  const [reloadCount, setReloadCount] = useState(0);

  useEffect(() => {
    if (userId === null) {
      setLoaded(null);
      setFailure(null);
      return;
    }
    const request = new AbortController();
    listWorkspaces({ signal: request.signal })
      .then(({ workspaces, last_used_workspace_id }) => {
        setLoaded((current) => ({
          userId,
          workspaces,
          activeId: chooseWorkspace(
            workspaces,
            current?.userId === userId ? current.activeId : null,
            last_used_workspace_id,
          ),
        }));
        setFailure(null);
      })
      .catch((error: unknown) => {
        if (request.signal.aborted) return;
        const message =
          error instanceof Error ? error.message : "Something went wrong.";
        setFailure({ userId, message });
      });
    return () => request.abort();
  }, [reloadCount, userId]);

  const activeId = loaded?.userId === userId ? loaded.activeId : null;

  // Losing access (or write access) to the active workspace refreshes the
  // memberships: a changed role updates in place, a removed one moves on.
  useEffect(() => {
    if (activeId === null) return;
    return subscribeWorkspaceAccessDenied((workspaceId) => {
      if (workspaceId === activeId) setReloadCount((count) => count + 1);
    });
  }, [activeId]);

  const retry = useCallback((): void => {
    setFailure(null);
    setReloadCount((count) => count + 1);
  }, []);

  const switchWorkspace = useCallback(
    (workspaceId: string): void => {
      if (
        loaded === null ||
        loaded.userId !== userId ||
        !loaded.workspaces.some((workspace) => workspace.id === workspaceId)
      ) {
        return;
      }
      setLoaded({ ...loaded, activeId: workspaceId });
      // Opening the workspace does not wait on this; if it fails, the next
      // visit just falls back to the previous choice or the selection page.
      void markWorkspaceLastUsed(workspaceId).catch(() => undefined);
    },
    [loaded, userId],
  );

  // Leaves the active workspace for the selection page. The server's last-used
  // workspace is untouched, so a reload still reopens it.
  const showAllWorkspaces = useCallback((): void => {
    setLoaded((current) =>
      current?.userId === userId ? { ...current, activeId: null } : current,
    );
  }, [userId]);

  const state = useMemo<WorkspaceState>(() => {
    if (userId === null) return { status: "idle" };
    if (loaded?.userId === userId) {
      const workspace = loaded.workspaces.find(
        (item) => item.id === loaded.activeId,
      );
      if (!workspace) {
        if (loaded.workspaces.length === 0) return { status: "empty" };
        return {
          status: "selecting",
          workspaces: loaded.workspaces,
          switchWorkspace,
        };
      }
      return {
        status: "ready",
        workspace,
        workspaces: loaded.workspaces,
        role: workspace.role,
        canWrite: workspace.role === "owner" || workspace.role === "editor",
        switchWorkspace,
        showAllWorkspaces,
      };
    }
    if (failure?.userId === userId) {
      return { status: "error", message: failure.message, retry };
    }
    return { status: "loading" };
  }, [failure, loaded, retry, showAllWorkspaces, switchWorkspace, userId]);

  return (
    <WorkspaceContext.Provider value={state}>{children}</WorkspaceContext.Provider>
  );
}

export function useWorkspaceState(): WorkspaceState {
  const state = useContext(WorkspaceContext);
  if (!state) {
    throw new Error("useWorkspaceState must be used inside WorkspaceProvider.");
  }
  return state;
}

// Pins one ready workspace for the screen rendered inside it. The value travels
// with the element, so a screen fading out after sign-out or a workspace switch
// keeps the workspace it was opened with instead of reading a newer state.
export function ActiveWorkspaceProvider({
  workspace,
  children,
}: {
  workspace: ActiveWorkspace;
  children: ReactNode;
}) {
  return (
    <ActiveWorkspaceContext.Provider value={workspace}>
      {children}
    </ActiveWorkspaceContext.Provider>
  );
}

// For features inside the signed-in app: every graph request needs it.
export function useWorkspace(): ActiveWorkspace {
  const workspace = useContext(ActiveWorkspaceContext);
  if (!workspace) {
    throw new Error("useWorkspace must be used inside ActiveWorkspaceProvider.");
  }
  return workspace;
}
