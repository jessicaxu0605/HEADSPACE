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
};

export type WorkspaceState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string; retry: () => void }
  | { status: "empty" }
  | ({ status: "ready" } & ActiveWorkspace);

type LoadedWorkspaces = {
  userId: string;
  workspaces: Workspace[];
  activeId: string | null;
};

const WorkspaceContext = createContext<WorkspaceState | null>(null);
const ActiveWorkspaceContext = createContext<ActiveWorkspace | null>(null);

// The last workspace is remembered per user so a shared browser never carries
// one account's choice into another.
const rememberedWorkspaceKey = (userId: string): string =>
  `headspace.workspace.${userId}`;

const readRememberedWorkspace = (userId: string): string | null => {
  try {
    return window.localStorage.getItem(rememberedWorkspaceKey(userId));
  } catch {
    return null;
  }
};

const rememberWorkspace = (userId: string, workspaceId: string | null): void => {
  try {
    if (workspaceId === null) {
      window.localStorage.removeItem(rememberedWorkspaceKey(userId));
    } else {
      window.localStorage.setItem(rememberedWorkspaceKey(userId), workspaceId);
    }
  } catch {
    // Without storage the first workspace is chosen on each visit.
  }
};

// Keep the current choice if it is still available, then the remembered one,
// then the oldest membership. A new account has exactly one workspace, the
// default one created at signup, so that one becomes active and remembered.
const chooseWorkspace = (
  userId: string,
  workspaces: Workspace[],
  currentId: string | null,
): string | null => {
  const available = new Set(workspaces.map((workspace) => workspace.id));
  const rememberedId = readRememberedWorkspace(userId);
  const activeId =
    [currentId, rememberedId].find((id) => id !== null && available.has(id)) ??
    workspaces[0]?.id ??
    null;
  rememberWorkspace(userId, activeId);
  return activeId;
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
      .then((workspaces) => {
        setLoaded((current) => ({
          userId,
          workspaces,
          activeId: chooseWorkspace(
            userId,
            workspaces,
            current?.userId === userId ? current.activeId : null,
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
      if (userId === null) return;
      setLoaded((current) => {
        if (
          !current ||
          current.userId !== userId ||
          !current.workspaces.some((workspace) => workspace.id === workspaceId)
        ) {
          return current;
        }
        rememberWorkspace(userId, workspaceId);
        return { ...current, activeId: workspaceId };
      });
    },
    [userId],
  );

  const state = useMemo<WorkspaceState>(() => {
    if (userId === null) return { status: "idle" };
    if (loaded?.userId === userId) {
      const workspace = loaded.workspaces.find(
        (item) => item.id === loaded.activeId,
      );
      if (!workspace) return { status: "empty" };
      return {
        status: "ready",
        workspace,
        workspaces: loaded.workspaces,
        role: workspace.role,
        canWrite: workspace.role === "owner" || workspace.role === "editor",
        switchWorkspace,
      };
    }
    if (failure?.userId === userId) {
      return { status: "error", message: failure.message, retry };
    }
    return { status: "loading" };
  }, [failure, loaded, retry, switchWorkspace, userId]);

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
