import { useCallback, useEffect, useRef, useState } from "react";
import {
  getNodeNeighborhood,
  type NodeNeighborhoodResponse,
} from "../api";
import type { MemoryNodeReference } from "../memory_graph";

const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

export const mergeNeighborhood = (
  current: NodeNeighborhoodResponse,
  incoming: NodeNeighborhoodResponse,
  anchorDistance: number,
): NodeNeighborhoodResponse => {
  const knownNodeIds = new Set(current.nodes.map((node) => node.node_id));
  const knownEdgeIds = new Set(current.edges.map((edge) => edge.edge_id));
  const newNodes = incoming.nodes
    .filter((node) => !knownNodeIds.has(node.node_id))
    .map((node) => ({
      ...node,
      distance: anchorDistance + node.distance,
    }));
  const newEdges = incoming.edges.filter(
    (edge) => !knownEdgeIds.has(edge.edge_id),
  );

  if (newNodes.length === 0 && newEdges.length === 0) return current;

  return {
    ...current,
    nodes: [...current.nodes, ...newNodes],
    edges: [...current.edges, ...newEdges],
  };
};

export default function useNeighborhoodExplorer() {
  const [selection, setSelection] =
    useState<MemoryNodeReference | null>(null);
  const [graph, setGraph] = useState<NodeNeighborhoodResponse | null>(null);
  const [status, setStatus] = useState("");
  const [statusIsError, setStatusIsError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [expandNotice, setExpandNotice] = useState("");
  const [expandNoticeIsError, setExpandNoticeIsError] = useState(false);
  const graphControllerRef = useRef<AbortController | null>(null);
  const expansionSessionRef = useRef(0);
  const expandRequestIdRef = useRef(0);
  const expandingNodeIdsRef = useRef(new Set<string>());
  const expandedNodeIdsRef = useRef(new Set<string>());

  const resetExpansions = useCallback((): void => {
    expansionSessionRef.current += 1;
    expandRequestIdRef.current += 1;
    expandingNodeIdsRef.current = new Set();
    expandedNodeIdsRef.current = new Set();
    setExpandNotice("");
    setExpandNoticeIsError(false);
  }, []);

  const close = useCallback((): void => {
    graphControllerRef.current?.abort();
    graphControllerRef.current = null;
    setSelection(null);
    setGraph(null);
    resetExpansions();
    setStatus("");
    setStatusIsError(false);
    setLoading(false);
  }, [resetExpansions]);

  useEffect(
    () => () => {
      graphControllerRef.current?.abort();
      expansionSessionRef.current += 1;
    },
    [],
  );

  const open = useCallback(
    async (nextSelection: MemoryNodeReference): Promise<void> => {
      graphControllerRef.current?.abort();
      const request = new AbortController();
      graphControllerRef.current = request;
      setSelection(nextSelection);
      setGraph(null);
      resetExpansions();
      setLoading(true);
      setStatus(`finding what’s connected to ${nextSelection.title}…`);
      setStatusIsError(false);

      try {
        const payload = await getNodeNeighborhood(nextSelection.node_id, {
          depth: 2,
          signal: request.signal,
        });
        if (graphControllerRef.current !== request) return;
        setGraph(payload);
        setStatus("");
        setStatusIsError(false);
      } catch (error) {
        if (!isAbortError(error) && graphControllerRef.current === request) {
          const message =
            error instanceof Error ? error.message : "Something went wrong.";
          setStatus(`Couldn’t load this node’s connections. ${message}`);
          setStatusIsError(true);
        }
      } finally {
        if (graphControllerRef.current === request) {
          graphControllerRef.current = null;
          setLoading(false);
        }
      }
    },
    [resetExpansions],
  );

  // Adds one more degree of connections around a node already in the open graph.
  const expand = useCallback(
    async (nodeSelection: MemoryNodeReference): Promise<void> => {
      if (!graph) return;
      const rootNodeId = graph.start_node_id;
      const nodeId = nodeSelection.node_id;
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
        setExpandNotice(`nothing more connected to ${nodeSelection.title}`);
        setExpandNoticeIsError(false);
        return;
      }

      const sessionId = expansionSessionRef.current;
      expandingNodeIdsRef.current.add(nodeId);
      setExpandNotice(`expanding ${nodeSelection.title}…`);
      setExpandNoticeIsError(false);
      const isCurrentExpansion = (): boolean =>
        expansionSessionRef.current === sessionId;
      const showNotice = (notice: string, isError = false): void => {
        if (isCurrentExpansion() && expandRequestIdRef.current === requestId) {
          setExpandNotice(notice);
          setExpandNoticeIsError(isError);
        }
      };

      try {
        const payload = await getNodeNeighborhood(nodeId, { depth: 1 });
        if (!isCurrentExpansion()) return;
        expandedNodeIdsRef.current.add(nodeId);

        const shownNodeIds = new Set(graph.nodes.map((node) => node.node_id));
        const foundNew = payload.nodes.some(
          (node) => !shownNodeIds.has(node.node_id),
        );
        showNotice(
          foundNew ? "" : `nothing more connected to ${nodeSelection.title}`,
        );

        setGraph((current) => {
          if (!current || current.start_node_id !== rootNodeId) return current;
          return mergeNeighborhood(current, payload, distance ?? 0);
        });
      } catch (error) {
        if (!isCurrentExpansion()) return;
        const message =
          error instanceof Error ? error.message : "Something went wrong.";
        showNotice(`couldn’t expand ${nodeSelection.title}. ${message}`, true);
      } finally {
        if (isCurrentExpansion()) expandingNodeIdsRef.current.delete(nodeId);
      }
    },
    [graph],
  );

  return {
    selection,
    graph,
    status,
    statusIsError,
    loading,
    expandNotice,
    expandNoticeIsError,
    open,
    expand,
    close,
  };
}
