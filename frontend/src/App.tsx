import { useEffect, useRef, useState } from "react";
import { queryNodes, type NodeQueryMatch } from "./api";
import ResultInterface from "./components/ResultInterface";
import SearchInterface from "./components/SearchInterface";
import TextEntryInterface from "./components/TextEntryInterface";
import useNeighborhoodExplorer from "./hooks/useNeighborhoodExplorer";

const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

export default function App() {
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

  const backToResults = (): void => {
    graphExplorer.close();
    window.requestAnimationFrame(() =>
      (selectedButtonRef.current ?? searchInputRef.current)?.focus(),
    );
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

  return (
    <main className={`page-stack${isGraphPage ? " is-graph-page" : ""}`}>
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
            onOpenNode={(selection) => {
              selectedNodeIdRef.current = selection.node_id;
              selectedButtonRef.current = null;
              void graphExplorer.open(selection);
            }}
          />
        </div>

        {!isGraphPage && (
          <button
            className="text-button continue-button"
            type="button"
            onClick={openEntry}
          >
            add a thought&nbsp; ↓
          </button>
        )}
      </section>

      <TextEntryInterface
        sectionRef={entrySectionRef}
        textareaRef={textareaRef}
        onViewRun={viewRun}
      />
    </main>
  );
}
