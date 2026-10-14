import { useCallback, useEffect, useRef, useState } from "react";
import {
  getNodeNeighborhood,
  queryNodes,
  type NodeNeighborhoodResponse,
  type NodeQueryMatch,
} from "./api";
import ResultInterface from "./components/ResultInterface";
import SearchInterface from "./components/SearchInterface";
import TextEntryInterface from "./components/TextEntryInterface";
import type { MemoryNodeReference } from "./memory_graph";

const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

export default function App() {
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<NodeQueryMatch[]>([]);
  const [searchStatus, setSearchStatus] = useState("");
  const [searching, setSearching] = useState(false);
  const [graphSelection, setGraphSelection] =
    useState<MemoryNodeReference | null>(null);
  const [graph, setGraph] = useState<NodeNeighborhoodResponse | null>(null);
  const [graphStatus, setGraphStatus] = useState("");
  const [graphLoading, setGraphLoading] = useState(false);
  const [expandNotice, setExpandNotice] = useState("");
  const expandRequestIdRef = useRef(0);
  const expandingNodeIdsRef = useRef(new Set<string>());
  const expandedNodeIdsRef = useRef(new Set<string>());
  const searchControllerRef = useRef<AbortController | null>(null);
  const graphControllerRef = useRef<AbortController | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const entrySectionRef = useRef<HTMLElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const selectedButtonRef = useRef<HTMLButtonElement | null>(null);
  const selectedNodeIdRef = useRef<string | null>(null);

  useEffect(
    () => () => {
      searchControllerRef.current?.abort();
      graphControllerRef.current?.abort();
    },
    [],
  );

  const resetExpansions = (): void => {
    expandRequestIdRef.current += 1;
    expandingNodeIdsRef.current = new Set();
    expandedNodeIdsRef.current = new Set();
    setExpandNotice("");
  };

  const openNodeGraph = useCallback(async (selection: MemoryNodeReference) => {
    graphControllerRef.current?.abort();
    const request = new AbortController();
    graphControllerRef.current = request;
    selectedNodeIdRef.current = selection.node_id;
    selectedButtonRef.current = null;
    setGraphSelection(selection);
    setGraph(null);
    resetExpansions();
    setGraphLoading(true);
    setGraphStatus(`finding what’s connected to ${selection.title}…`);

    try {
      const payload = await getNodeNeighborhood(selection.node_id, {
        depth: 2,
        signal: request.signal,
      });
      setGraph(payload);
      setGraphStatus("");
    } catch (error) {
      if (!isAbortError(error)) {
        const message =
          error instanceof Error ? error.message : "Something went wrong.";
        setGraphStatus(`Couldn’t load this node’s connections. ${message}`);
      }
    } finally {
      if (graphControllerRef.current === request) {
        graphControllerRef.current = null;
        setGraphLoading(false);
      }
    }
  }, []);

  // Adds one more degree of connections around a node already in the open graph.
  const expandNode = async (selection: MemoryNodeReference): Promise<void> => {
    if (!graph) return;
    const rootNodeId = graph.start_node_id;
    const nodeId = selection.node_id;
    if (expandingNodeIdsRef.current.has(nodeId)) return;

    // Everything closer than the loaded depth already has all its connections shown.
    const distance = graph.nodes.find(
      (node) => node.node_id === nodeId,
    )?.distance;
    const requestId = ++expandRequestIdRef.current;
    if (
      expandedNodeIdsRef.current.has(nodeId) ||
      (distance !== undefined && distance < graph.depth)
    ) {
      setExpandNotice(`nothing more connected to ${selection.title}`);
      return;
    }

    expandingNodeIdsRef.current.add(nodeId);
    setExpandNotice(`expanding ${selection.title}…`);
    // Only the most recent expansion (for the graph still open) updates the notice.
    const showNotice = (notice: string): void => {
      if (expandRequestIdRef.current === requestId) setExpandNotice(notice);
    };

    try {
      const payload = await getNodeNeighborhood(nodeId, { depth: 1 });
      expandedNodeIdsRef.current.add(nodeId);

      const shownNodeIds = new Set(graph.nodes.map((node) => node.node_id));
      const foundNew = payload.nodes.some(
        (node) => !shownNodeIds.has(node.node_id),
      );
      showNotice(
        foundNew ? "" : `nothing more connected to ${selection.title}`,
      );

      setGraph((current) => {
        // The user may have opened a different node while this was loading.
        if (!current || current.start_node_id !== rootNodeId) return current;

        const knownNodeIds = new Set(current.nodes.map((node) => node.node_id));
        const knownEdgeIds = new Set(current.edges.map((edge) => edge.edge_id));
        const newNodes = payload.nodes
          .filter((node) => !knownNodeIds.has(node.node_id))
          .map((node) => ({
            ...node,
            distance: (distance ?? 0) + node.distance,
          }));
        const newEdges = payload.edges.filter(
          (edge) => !knownEdgeIds.has(edge.edge_id),
        );
        if (newNodes.length === 0 && newEdges.length === 0) return current;

        return {
          ...current,
          nodes: [...current.nodes, ...newNodes],
          edges: [...current.edges, ...newEdges],
        };
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Something went wrong.";
      showNotice(`couldn’t expand ${selection.title}. ${message}`);
    } finally {
      expandingNodeIdsRef.current.delete(nodeId);
    }
  };

  const handleSearch = async (): Promise<void> => {
    const trimmedQuery = query.trim();
    if (!trimmedQuery) {
      searchControllerRef.current?.abort();
      searchControllerRef.current = null;
      setMatches([]);
      setSearchStatus("");
      searchInputRef.current?.focus();
      return;
    }

    searchControllerRef.current?.abort();
    const request = new AbortController();
    searchControllerRef.current = request;
    setSearching(true);
    setMatches([]);
    setSearchStatus("searching your headspace…");

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
    } catch (error) {
      if (!isAbortError(error)) {
        const message =
          error instanceof Error ? error.message : "Something went wrong.";
        setSearchStatus(`Couldn’t search your headspace. ${message}`);
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
    setQuery(nextQuery);
  };

  const backToResults = (): void => {
    graphControllerRef.current?.abort();
    graphControllerRef.current = null;
    setGraphSelection(null);
    setGraph(null);
    resetExpansions();
    setGraphStatus("");
    setGraphLoading(false);
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

  const isGraphPage = graphSelection !== null;

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
            expandNotice={expandNotice}
            graph={graph}
            graphLoading={graphLoading}
            graphSelection={graphSelection}
            graphStatus={graphStatus}
            matches={matches}
            searchStatus={searchStatus}
            selectedButtonRef={selectedButtonRef}
            selectedNodeIdRef={selectedNodeIdRef}
            onBack={backToResults}
            onExpandNode={(selection) => void expandNode(selection)}
            onOpenNode={(selection) => void openNodeGraph(selection)}
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
      />
    </main>
  );
}
