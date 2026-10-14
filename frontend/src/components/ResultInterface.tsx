import { useEffect, useRef, useState, type RefObject } from "react";
import type { NodeNeighborhoodResponse, NodeQueryMatch } from "../api";
import type { MemoryNodeReference } from "../memory_graph";
import BlurredContainer from "./BlurredContainer";
import NeighborhoodGraph from "./NeighborhoodGraph";
import RelationshipPage from "./RelationshipPage";

type ResultInterfaceProps = {
  expandNotice: string;
  expandNoticeIsError: boolean;
  graph: NodeNeighborhoodResponse | null;
  graphLoading: boolean;
  graphSelection: MemoryNodeReference | null;
  graphStatus: string;
  graphStatusIsError: boolean;
  matches: NodeQueryMatch[];
  searchStatus: string;
  searchStatusIsError: boolean;
  selectedButtonRef: RefObject<HTMLButtonElement | null>;
  selectedNodeIdRef: RefObject<string | null>;
  onBack: () => void;
  onExpandNode: (selection: MemoryNodeReference) => void;
  onOpenNode: (selection: MemoryNodeReference) => void;
};

export default function ResultInterface({
  expandNotice,
  expandNoticeIsError,
  graph,
  graphLoading,
  graphSelection,
  graphStatus,
  graphStatusIsError,
  matches,
  searchStatus,
  searchStatusIsError,
  selectedButtonRef,
  selectedNodeIdRef,
  onBack,
  onExpandNode,
  onOpenNode,
}: ResultInterfaceProps) {
  const graphHeaderRef = useRef<HTMLElement | null>(null);
  // The relationship page covers the graph rather than replacing it, so the
  // graph keeps its layout and zoom for when the reader comes back.
  const [openEdgeId, setOpenEdgeId] = useState<string | null>(null);
  const graphNodeId = graphSelection?.node_id ?? null;

  useEffect(() => {
    setOpenEdgeId(null);
  }, [graphNodeId]);

  if (!graphSelection) {
    return (
      <div className="search-feedback" aria-live="polite">
        {searchStatus && (
          <p
            className={`search-status${
              searchStatusIsError ? " error-message" : ""
            }`}
            role={searchStatusIsError ? "alert" : undefined}
          >
            {searchStatus}
          </p>
        )}
        <ol className="search-results" aria-label="Search results">
          {matches.map((match) => {
            const aliases = match.identifiers.filter(
              (identifier) =>
                identifier.localeCompare(match.title, undefined, {
                  sensitivity: "accent",
                }) !== 0,
            );

            return (
              <li className="search-result" key={match.node_id}>
                <button
                  ref={(element) => {
                    if (selectedNodeIdRef.current === match.node_id) {
                      selectedButtonRef.current = element;
                    }
                  }}
                  className="search-result-button"
                  type="button"
                  onClick={(event) => {
                    selectedNodeIdRef.current = match.node_id;
                    selectedButtonRef.current = event.currentTarget;
                    onOpenNode(match);
                  }}
                >
                  <span className="search-result-title">{match.title}</span>
                  <span className="search-result-meta">
                    {match.node_type} · {Math.round(match.match_score * 100)}%
                    match
                  </span>
                  {aliases.length > 0 && (
                    <span className="search-result-aliases">
                      also known as {aliases.slice(0, 3).join(", ")}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    );
  }

  const nodesById = new Map(graph?.nodes.map((node) => [node.node_id, node]));
  const startNode = graph ? nodesById.get(graph.start_node_id) : undefined;
  const connectedCount =
    graph?.nodes.filter((node) => node.node_id !== graph.start_node_id)
      .length ?? 0;

  return (
    <>
      <section
        className={`graph-view${graphLoading ? " is-loading" : ""}`}
        aria-live="polite"
        inert={openEdgeId !== null}
      >
        <BlurredContainer
          blurredEdges={["bottom"]}
          edgeBlurSize={24}
          className="graph-topbar"
        >
          <button className="graph-back" type="button" onClick={onBack}>
            ← back to results
          </button>
        </BlurredContainer>
        {graphStatus && (
          <p
            className={`graph-status${
              graphStatusIsError ? " error-message" : ""
            }`}
            role={graphStatusIsError ? "alert" : "status"}
          >
            {graphStatus}
          </p>
        )}
        {graph && (
          <div className="graph-body">
            <BlurredContainer
              as="header"
              blurredEdges={["top", "right", "bottom", "left"]}
              edgeBlurSize={28}
              className="graph-header"
              ref={graphHeaderRef}
            >
              <p className="graph-kicker">
                {startNode?.node_type ?? graphSelection.node_type}
              </p>
              <h2 className="graph-title">
                {startNode?.title ?? graphSelection.title}
              </h2>
              {startNode?.summary && (
                <p className="graph-summary">{startNode.summary}</p>
              )}
            </BlurredContainer>

            <section className="graph-section">
              {connectedCount === 0 ? (
                <p className="graph-empty">no connected nodes yet.</p>
              ) : (
                <NeighborhoodGraph
                  graph={graph}
                  overlayRef={graphHeaderRef}
                  onExpandNode={onExpandNode}
                  onOpenNode={onOpenNode}
                  onOpenEdge={setOpenEdgeId}
                />
              )}
              <p
                className={`graph-depth${
                  expandNoticeIsError ? " error-message" : ""
                }`}
                role={expandNoticeIsError ? "alert" : undefined}
              >
                {expandNotice ||
                  `within ${graph.depth} ${graph.depth === 1 ? "degree" : "degrees"}`}
              </p>
            </section>
          </div>
        )}
      </section>
      {openEdgeId && (
        <RelationshipPage
          edgeId={openEdgeId}
          onBack={() => setOpenEdgeId(null)}
          onOpenNode={(selection) => {
            setOpenEdgeId(null);
            onOpenNode(selection);
          }}
        />
      )}
    </>
  );
}
