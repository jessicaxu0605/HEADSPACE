import { useEffect, useRef, useState } from "react";
import { clearAccessToken, queryNodes, type NodeQueryMatch } from "./api";
import AuthFlow from "./features/AuthFlow";
import ResultInterface from "./features/ResultInterface";
import SearchInterface from "./features/SearchInterface";
import SurfacedMemories from "./features/SurfacedMemories";
import TextEntryInterface from "./features/TextEntryInterface";
import { PageTransition, TextButton } from "./component-library";
import useNeighborhoodExplorer from "./hooks/useNeighborhoodExplorer";
import type { MemoryNodeReference } from "./memory_graph";

const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

export default function App() {
  const [authenticated, setAuthenticated] = useState(false);

  return (
    <PageTransition
      className="app-state-transition"
      pageKey={authenticated ? "authenticated" : "auth"}
    >
      {authenticated ? (
        <AuthenticatedApp
          onLogout={() => {
            clearAccessToken();
            setAuthenticated(false);
          }}
        />
      ) : (
        <AuthFlow onAuthenticated={() => setAuthenticated(true)} />
      )}
    </PageTransition>
  );
}

function AuthenticatedApp({ onLogout }: { onLogout: () => void }) {
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<NodeQueryMatch[]>([]);
  const [searchStatus, setSearchStatus] = useState("");
  const [searchStatusIsError, setSearchStatusIsError] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchFocused, setSearchFocused] = useState(false);
  const searchControllerRef = useRef<AbortController | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const entrySectionRef = useRef<HTMLElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const selectedButtonRef = useRef<HTMLButtonElement | null>(null);
  const selectedNodeIdRef = useRef<string | null>(null);
  const restoreResultsFocusRef = useRef(false);
  const graphExplorer = useNeighborhoodExplorer();

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
      const payload = await queryNodes(trimmedQuery, {
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

  const backToResults = (): void => {
    restoreResultsFocusRef.current = true;
    graphExplorer.close();
  };

  const openEntry = (): void => {
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
    // The graph page hides the entry section, so jump rather than animate.
    window.scrollTo({ top: 0 });
    void graphExplorer.openRun(runId);
  };

  const isGraphPage = graphExplorer.selection !== null;
  const hideContinueButton =
    (query.length > 0 && searchFocused) || matches.length > 0;
  const hideSurfacedMemories =
    searchFocused || matches.length > 0 || searchStatus !== "";

  return (
    <PageTransition
      appear={false}
      className="authenticated-page-transition"
      pageKey={isGraphPage ? "graph" : "landing"}
      onSwitched={(transitionKey) => {
        if (transitionKey !== "landing" || !restoreResultsFocusRef.current) {
          return;
        }
        restoreResultsFocusRef.current = false;
        // Focusing an empty search would hide surfaced memories, so only
        // return there when search feedback is already being shown.
        const showingSearch = matches.length > 0 || searchStatus !== "";
        (
          selectedButtonRef.current ??
          (showingSearch ? searchInputRef.current : null)
        )?.focus();
      }}
    >
      <main className={`page-stack${isGraphPage ? " is-graph-page" : ""}`}>
      <button className="session-logout" type="button" onClick={onLogout}>
        log out
      </button>
      <section className="landing screen" aria-labelledby="search-title">
        <div className="search-wrap">
          <h1 id="search-title" className="sr-only">
            Search your headspace
          </h1>
          {!isGraphPage && (
            <SurfacedMemories
              hidden={hideSurfacedMemories}
              onOpenNode={openSurfacedMemory}
            />
          )}
          {!isGraphPage && (
            <SearchInterface
              inputRef={searchInputRef}
              query={query}
              searching={searching}
              onQueryChange={handleQueryChange}
              onFocusChange={setSearchFocused}
              onSearch={() => void handleSearch()}
            />
          )}
          <ResultInterface
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
            onBack={backToResults}
            onExpandNode={(selection) => void graphExplorer.expand(selection)}
            onOpenNode={openNode}
          />
        </div>

        {!isGraphPage && (
          <div className="continue-wrap">
            <TextButton
              className={`continue-button${hideContinueButton ? " is-search-active" : ""}`}
              aria-hidden={hideContinueButton ? true : undefined}
              tabIndex={hideContinueButton ? -1 : undefined}
              onClick={openEntry}
            >
              add a thought&nbsp; ↓
            </TextButton>
          </div>
        )}
      </section>

      <TextEntryInterface
        sectionRef={entrySectionRef}
        textareaRef={textareaRef}
        onViewRun={viewRun}
      />
      </main>
    </PageTransition>
  );
}
