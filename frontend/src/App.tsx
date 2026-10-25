import { useEffect, useRef, useState, type ReactNode } from "react";
import { queryNodes, type NodeQueryMatch } from "./api";
import AuthFlow from "./features/AuthFlow";
import ResultInterface from "./features/ResultInterface";
import SearchInterface from "./features/SearchInterface";
import SessionMenu from "./features/SessionMenu";
import SurfacedMemories from "./features/SurfacedMemories";
import TextEntryInterface from "./features/TextEntryInterface";
import WorkspaceSelection from "./features/WorkspaceSelection";
import WorkspaceWelcome from "./features/WorkspaceWelcome";
import WorkspaceUnavailable from "./features/WorkspaceUnavailable";
import { BrainLoader, PageTransition, TextButton } from "./component-library";
import useNeighborhoodExplorer from "./hooks/useNeighborhoodExplorer";
import type { MemoryNodeReference } from "./memory_graph";
import { SessionProvider, useSession } from "./providers/SessionProvider";
import {
  ActiveWorkspaceProvider,
  WorkspaceProvider,
  useWorkspace,
  useWorkspaceState,
} from "./providers/WorkspaceProvider";

const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

export default function App() {
  return (
    <SessionProvider>
      <WorkspaceProvider>
        <AppScreens />
      </WorkspaceProvider>
    </SessionProvider>
  );
}

// Chooses the one screen the session and workspace state call for. Each screen
// has its own page key, so switching workspaces also remounts the app.
function AppScreens() {
  const session = useSession();
  const workspaceState = useWorkspaceState();

  let pageKey: string;
  let screen: ReactNode;
  if (session.status === "signed-out") {
    pageKey = "auth";
    screen = <AuthFlow onAuthenticated={session.signIn} />;
  } else if (workspaceState.status === "error") {
    pageKey = "workspace-error";
    screen = (
      <WorkspaceUnavailable
        error={workspaceState.message}
        onRetry={workspaceState.retry}
        onLogout={session.signOut}
      />
    );
  } else if (workspaceState.status === "empty") {
    pageKey = "workspace-empty";
    screen = <WorkspaceUnavailable onLogout={session.signOut} />;
  } else if (workspaceState.status === "selecting") {
    pageKey = "workspace-selection";
    screen = (
      <WorkspaceSelection
        workspaces={workspaceState.workspaces}
        onSelect={workspaceState.switchWorkspace}
        onCreate={workspaceState.createWorkspace}
        onLogout={session.signOut}
      />
    );
  } else if (workspaceState.status === "ready") {
    pageKey = `workspace:${workspaceState.workspace.id}`;
    screen = (
      <ActiveWorkspaceProvider workspace={workspaceState}>
        <AuthenticatedApp
          key={workspaceState.workspace.id}
          onLogout={session.signOut}
        />
      </ActiveWorkspaceProvider>
    );
  } else {
    // Checking the stored session, then loading its workspaces.
    pageKey = "loading";
    screen = <BrainLoader label="Opening your headspace…" />;
  }

  return (
    <PageTransition className="app-state-transition" pageKey={pageKey}>
      {screen}
    </PageTransition>
  );
}


// The signed-in pages besides the welcome page. The feed is the landing page;
// search and adding a memory each have their own page, reached from the
// top-right actions.
type View = "feed" | "search" | "entry";

function AuthenticatedApp({ onLogout }: { onLogout: () => void }) {
  const [view, setView] = useState<View>("feed");
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<NodeQueryMatch[]>([]);
  const [searchStatus, setSearchStatus] = useState("");
  const [searchStatusIsError, setSearchStatusIsError] = useState(false);
  const [searching, setSearching] = useState(false);
  const searchControllerRef = useRef<AbortController | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const entrySectionRef = useRef<HTMLElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const selectedButtonRef = useRef<HTMLButtonElement | null>(null);
  const selectedNodeIdRef = useRef<string | null>(null);
  const restoreResultsFocusRef = useRef(false);
  const { workspace, canWrite, showAllWorkspaces, newWorkspace } = useWorkspace();
  const graphExplorer = useNeighborhoodExplorer();
  // A just-created workspace opens on its welcome page until the reader views
  // what they add; it is never shown again after that.
  const [showingWelcome, setShowingWelcome] = useState(newWorkspace !== null);

  useEffect(
    () => () => {
      searchControllerRef.current?.abort();
    },
    [],
  );

  const handleSearch = async (): Promise<void> => {
    const trimmedQuery = query.trim();
    if (!trimmedQuery) {
      searchControllerRef.current?.abort();
      searchControllerRef.current = null;
      setMatches([]);
      setSearchStatus("");
      setSearchStatusIsError(false);
      searchInputRef.current?.focus();
      return;
    }

    searchControllerRef.current?.abort();
    const request = new AbortController();
    searchControllerRef.current = request;
    setSearching(true);
    setMatches([]);
    setSearchStatus("searching your headspace…");
    setSearchStatusIsError(false);

    try {
      const payload = await queryNodes(workspace.id, trimmedQuery, {
        limit: 5,
        signal: request.signal,
      });
      setMatches(payload.matches);
      setSearchStatus(
        payload.matches.length === 0
          ? `nothing surfaced for “${trimmedQuery}”.`
          : "",
      );
      setSearchStatusIsError(false);
    } catch (error) {
      if (!isAbortError(error)) {
        const message =
          error instanceof Error ? error.message : "Something went wrong.";
        setSearchStatus(`Couldn’t search your headspace. ${message}`);
        setSearchStatusIsError(true);
      }
    } finally {
      if (searchControllerRef.current === request) {
        searchControllerRef.current = null;
        setSearching(false);
      }
    }
  };

  const handleQueryChange = (nextQuery: string): void => {
    searchControllerRef.current?.abort();
    searchControllerRef.current = null;
    setSearching(false);
    setMatches([]);
    setSearchStatus("");
    setSearchStatusIsError(false);
    setQuery(nextQuery);
  };

  const openNode = (selection: MemoryNodeReference): void => {
    selectedNodeIdRef.current = selection.node_id;
    selectedButtonRef.current = null;
    void graphExplorer.open(selection);
  };

  const openSurfacedMemory = (selection: MemoryNodeReference): void => {
    window.scrollTo({ top: 0 });
    openNode(selection);
  };

  // Closing the graph returns to whichever page opened it.
  const backFromGraph = (): void => {
    restoreResultsFocusRef.current = true;
    graphExplorer.close();
  };

  const showView = (nextView: View): void => {
    window.scrollTo({ top: 0 });
    graphExplorer.close();
    setView(nextView);
  };

  // The welcome page keeps the entry below it; everywhere else it is a page.
  const openEntry = (): void => {
    if (!showingWelcome) {
      showView("entry");
      return;
    }
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    entrySectionRef.current?.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "start",
    });
    if (reduceMotion) {
      textareaRef.current?.focus({ preventScroll: true });
    } else {
      window.setTimeout(
        () => textareaRef.current?.focus({ preventScroll: true }),
        700,
      );
    }
  };

  const viewRun = (runId: string): void => {
    selectedNodeIdRef.current = null;
    selectedButtonRef.current = null;
    // The graph page hides the entry form, so jump rather than animate.
    window.scrollTo({ top: 0 });
    if (showingWelcome) {
      setShowingWelcome(false);
      setView("feed");
    }
    void graphExplorer.openRun(runId);
  };

  const isGraphPage = !showingWelcome && graphExplorer.selection !== null;
  const pageKey = showingWelcome ? "welcome" : isGraphPage ? "graph" : view;
  const backLabel =
    graphExplorer.selection?.kind !== "run" && view === "search"
      ? "← back to results"
      : "← back";

  return (
    <PageTransition
      appear={false}
      className="authenticated-page-transition"
      pageKey={pageKey}
      onSwitched={(transitionKey) => {
        if (transitionKey === "entry") {
          textareaRef.current?.focus({ preventScroll: true });
          return;
        }
        if (transitionKey === "search" && !restoreResultsFocusRef.current) {
          searchInputRef.current?.focus();
          return;
        }
        if (transitionKey === "graph" || !restoreResultsFocusRef.current) {
          return;
        }
        restoreResultsFocusRef.current = false;
        (
          selectedButtonRef.current ??
          (transitionKey === "search" ? searchInputRef.current : null)
        )?.focus();
      }}
    >
      <main className={`page-stack${isGraphPage ? " is-graph-page" : ""}`}>
      <SessionMenu
        onLogout={onLogout}
        onShowWorkspaces={showAllWorkspaces}
        actions={
          showingWelcome ? undefined : (
            <>
              {(isGraphPage || view !== "search") && (
                <TextButton size="s" onClick={() => showView("search")}>
                  <svg
                    className="text-button-icon"
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                  >
                    <circle cx="11" cy="11" r="6.5" />
                    <path d="m16 16 4 4" />
                  </svg>
                  search
                </TextButton>
              )}
              {canWrite && (isGraphPage || view !== "entry") && (
                <TextButton size="s" onClick={() => showView("entry")}>
                  <svg
                    className="text-button-icon"
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                  >
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                  add a memory
                </TextButton>
              )}
            </>
          )
        }
      />
      {!showingWelcome && !isGraphPage && view !== "feed" && (
        <div className="page-back">
          <TextButton size="s" onClick={() => showView("feed")}>
            ← back
          </TextButton>
        </div>
      )}
      {showingWelcome ? (
        <WorkspaceWelcome
          selfNodeId={newWorkspace?.selfNodeId ?? null}
          onAddMemories={canWrite ? openEntry : undefined}
        />
      ) : isGraphPage || view === "search" ? (
        <section className="landing screen" aria-labelledby="search-title">
          <div className="search-wrap">
            <h1 id="search-title" className="sr-only">
              Search your headspace
            </h1>
            {!isGraphPage && (
              <SearchInterface
                inputRef={searchInputRef}
                query={query}
                searching={searching}
                onQueryChange={handleQueryChange}
                onSearch={() => void handleSearch()}
              />
            )}
            <ResultInterface
              backLabel={backLabel}
              expandNotice={graphExplorer.expandNotice}
              graph={graphExplorer.graph}
              graphLoading={graphExplorer.loading}
              graphSelection={graphExplorer.selection}
              graphStatus={graphExplorer.status}
              graphStatusIsError={graphExplorer.statusIsError}
              expandNoticeIsError={graphExplorer.expandNoticeIsError}
              matches={matches}
              searchStatus={searchStatus}
              searchStatusIsError={searchStatusIsError}
              selectedButtonRef={selectedButtonRef}
              selectedNodeIdRef={selectedNodeIdRef}
              onBack={backFromGraph}
              onExpandNode={(selection) => void graphExplorer.expand(selection)}
              onOpenNode={openNode}
            />
          </div>
        </section>
      ) : view === "feed" ? (
        <section className="landing screen" aria-labelledby="feed-title">
          <div className="search-wrap">
            <h1 id="feed-title" className="sr-only">
              Your headspace
            </h1>
            <SurfacedMemories onOpenNode={openSurfacedMemory} />
          </div>
        </section>
      ) : null}

      {/* Always mounted so pending submissions and their notifications
          outlive the entry page. */}
      {canWrite && (
        <TextEntryInterface
          sectionRef={entrySectionRef}
          textareaRef={textareaRef}
          formHidden={!showingWelcome && (isGraphPage || view !== "entry")}
          prompt={showingWelcome ? "What’s this headspace for?" : undefined}
          onViewRun={viewRun}
        />
      )}
      </main>
    </PageTransition>
  );
}
