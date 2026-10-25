import { useEffect, useState } from "react";
import { getNodeNeighborhood, type NodeNeighborhoodResponse } from "../api";
import { BrainLoader, PresenceTransition, TextButton } from "../component-library";
import { waitForBrainLoaderFade, waitForBrainLoaderLeadIn } from "../loading";
import { useWorkspace } from "../providers/WorkspaceProvider";
import NeighborhoodGraph from "./NeighborhoodGraph";

type WorkspaceWelcomeProps = {
  // The user's own node, when they chose to be added to the workspace.
  selfNodeId: string | null;
  // Scrolls to the memory entry below; omitted when the reader cannot write.
  onAddMemories?: () => void;
};

const ignoreSelection = (): void => undefined;

// The page waits behind the brain loader for the self node, then the loader
// fades before the page appears, so it never shows half-empty.
type LoadPhase = "loading" | "fading" | "ready";

// The first page of a just-created workspace: its whole graph (at most the
// user's own node), its name, and an invitation to start adding memories.
export default function WorkspaceWelcome({
  selfNodeId,
  onAddMemories,
}: WorkspaceWelcomeProps) {
  const { workspace } = useWorkspace();
  const [graph, setGraph] = useState<NodeNeighborhoodResponse | null>(null);
  const [phase, setPhase] = useState<LoadPhase>(
    selfNodeId === null ? "ready" : "loading",
  );

  // A new workspace has no edges, so the self node's neighborhood is all of it.
  useEffect(() => {
    if (selfNodeId === null) return;
    const loaderStartedAt = performance.now();
    const request = new AbortController();
    const load = async (): Promise<void> => {
      try {
        const payload = await getNodeNeighborhood(workspace.id, selfNodeId, {
          depth: 1,
          signal: request.signal,
        });
        setGraph(payload);
      } catch {
        // The page still works without the graph, so a failure just omits it.
        if (request.signal.aborted) return;
      }
      await waitForBrainLoaderLeadIn(loaderStartedAt);
      if (request.signal.aborted) return;
      setPhase("fading");
      await waitForBrainLoaderFade();
      if (request.signal.aborted) return;
      setPhase("ready");
    };
    void load();
    return () => request.abort();
  }, [selfNodeId, workspace.id]);

  const ready = phase === "ready";

  return (
    <section
      className="workspace-welcome screen"
      aria-labelledby="workspace-welcome-title"
      aria-busy={!ready}
    >
      {!ready && (
        <BrainLoader
          fading={phase === "fading"}
          label="Opening your new headspace…"
        />
      )}
      <PresenceTransition
        className="workspace-welcome-graph"
        enter="fade-up"
        visible={ready && graph !== null && graph.nodes.length > 0}
      >
        {graph && (
          <NeighborhoodGraph
            graph={graph}
            onExpandNode={ignoreSelection}
            onOpenNode={ignoreSelection}
            onOpenEdge={ignoreSelection}
          />
        )}
      </PresenceTransition>
      <PresenceTransition
        as="div"
        className="workspace-welcome-intro"
        enter="fade-up"
        unmountOnExit={false}
        visible={ready}
      >
        <h1 id="workspace-welcome-title" className="graph-title">
          {workspace.name}
        </h1>
        {onAddMemories && (
          <TextButton className="continue-button" onClick={onAddMemories}>
            add some memories&nbsp; ↓
          </TextButton>
        )}
      </PresenceTransition>
    </section>
  );
}
