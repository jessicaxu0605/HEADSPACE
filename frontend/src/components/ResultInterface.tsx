import {
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import type { NodeNeighborhoodResponse, NodeQueryMatch } from "../api";
import type { GraphSelection } from "../hooks/useNeighborhoodExplorer";
import type { MemoryNodeReference } from "../memory_graph";
import {
  BlurredContainer,
  FadeTransition,
  PresenceTransition,
  motionDurations,
  motionStaggers,
} from "../component-library";
import BrainLoader from "./BrainLoader";
import NeighborhoodGraph from "./NeighborhoodGraph";
import NodeDetails from "./NodeDetails";
import RelationshipPage from "./RelationshipPage";

type ResultInterfaceProps = {
  expandNotice: string;
  expandNoticeIsError: boolean;
  graph: NodeNeighborhoodResponse | null;
  graphLoading: boolean;
  graphSelection: GraphSelection | null;
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
  const graphSelectionKey =
    graphSelection === null
      ? null
      : graphSelection.kind === "run"
        ? `run:${graphSelection.run_id}`
        : `node:${graphSelection.node_id}`;

  useEffect(() => {
    setOpenEdgeId(null);
  }, [graphSelectionKey]);

  if (!graphSelection) {
    return (
      <div className="search-feedback" aria-live="polite">
        <PresenceTransition
          as="p"
          className={`search-status${
            searchStatusIsError ? " error-message" : ""
          }`}
          enter="fade-up"
          exit="fade-up"
          role={searchStatusIsError ? "alert" : undefined}
          visible={Boolean(searchStatus)}
        >
          {searchStatus}
        </PresenceTransition>
        <ol className="search-results" aria-label="Search results">
          {matches.map((match, index) => {
            const aliases = match.identifiers.filter(
              (identifier) =>
                identifier.localeCompare(match.title, undefined, {
                  sensitivity: "accent",
                }) !== 0,
            );

            return (
              <PresenceTransition
                as="li"
                className="search-result"
                enter="fade-up"
                enterDelay={index * motionStaggers.quick}
                exit="fade-up"
                key={match.node_id}
                visible
              >
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
              </PresenceTransition>
            );
          })}
        </ol>
      </div>
    );
  }

  const isRunView = graphSelection.kind === "run";
  const nodesById = new Map(graph?.nodes.map((node) => [node.node_id, node]));
  const startNode = graph ? nodesById.get(graph.start_node_id) : undefined;
  const connectedCount =
    graph?.nodes.filter((node) => node.node_id !== graph.start_node_id)
      .length ?? 0;
  const runNodeCount = graph?.nodes.length ?? 0;

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
            {isRunView ? "← back" : "← back to results"}
          </button>
        </BlurredContainer>
        {graphLoading && (
          <BrainLoader
            fading={graph !== null}
            label={graphStatus || "Loading graph…"}
          />
        )}
        <PresenceTransition
          as="p"
          className={`graph-status${
            graphStatusIsError ? " error-message" : ""
          }`}
          enter="fade-up"
          exit="fade-up"
          role={graphStatusIsError ? "alert" : "status"}
          visible={!graphLoading && Boolean(graphStatus)}
        >
          {graphStatus}
        </PresenceTransition>
        <PresenceTransition
          className="graph-body-transition"
          enter="fade-up"
          exit="fade-up"
          visible={graph !== null}
        >
          {graph && (
          <div className="graph-body">
            <BlurredContainer
              as="header"
              blurredEdges={["top", "right", "bottom", "left"]}
              edgeBlurSize={28}
              className="graph-header"
              ref={graphHeaderRef}
            >
              {graphSelection.kind === "run" ? (
                <>
                  <h2 className="graph-title">what you just shared</h2>
                  <p className="graph-summary">
                    {runNodeCount === 1
                      ? "1 thing remembered"
                      : `${runNodeCount} things remembered`}
                  </p>
                </>
              ) : (
                <>
                  <p className="graph-kicker">
                    {startNode?.node_type ?? graphSelection.node_type}
                  </p>
                  <h2 className="graph-title">
                    {startNode?.title ?? graphSelection.title}
                  </h2>
                  {startNode?.summary && (
                    <p className="graph-summary">{startNode.summary}</p>
                  )}
                  {startNode && (
                    <NodeDetails
                      aliases={startNode.aliases}
                      externalIds={startNode.external_ids}
                      details={startNode.details}
                    />
                  )}
                </>
              )}
            </BlurredContainer>

            <section className="graph-section">
              {isRunView && runNodeCount === 0 ? (
                <p className="graph-empty">nothing new was remembered.</p>
              ) : !isRunView && connectedCount === 0 ? (
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
              <PresenceTransition
                as="p"
                className={`graph-depth${
                  expandNoticeIsError ? " error-message" : ""
                }`}
                enter="fade-up"
                exit="fade-up"
                role={expandNoticeIsError ? "alert" : undefined}
                visible={Boolean(expandNotice)}
              >
                {expandNotice}
              </PresenceTransition>
            </section>
          </div>
          )}
        </PresenceTransition>
      </section>
      <FadeTransition
        className="relationship-page-transition"
        duration={motionDurations.page}
        visible={openEdgeId !== null}
      >
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
      </FadeTransition>
    </>
  );
}
