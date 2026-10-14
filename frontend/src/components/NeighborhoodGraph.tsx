import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import ForceGraph2D, {
  type ForceGraphMethods,
  type LinkObject,
  type NodeObject,
} from "react-force-graph-2d";
import type { NodeNeighborhoodResponse } from "../api";
import type {
  MemoryNeighborhoodNode,
  MemoryNodeReference,
} from "../memory_graph";

type GraphNode = MemoryNeighborhoodNode & { id: string };

type GraphLink = {
  source: string;
  target: string;
  edge_id: string;
  label: string;
  curvature: number;
};

type NeighborhoodGraphProps = {
  graph: NodeNeighborhoodResponse;
  // Element that may float over the left of the canvas; the initial fit keeps clear of it.
  overlayRef?: RefObject<HTMLElement | null>;
  onExpandNode: (selection: MemoryNodeReference) => void;
  onOpenNode: (selection: MemoryNodeReference) => void;
  onOpenEdge: (edgeId: string) => void;
};

type SimulationNode = NodeObject<GraphNode>;

const LABEL_FONT = 'Iowan Old Style, Baskerville, "Times New Roman", serif';
const NODE_RADIUS = 1.25;
const FIT_PADDING = 56;
const MAX_FIT_ZOOM = 5;
const LAYOUT_TICKS = 120;
const DOUBLE_CLICK_MS = 250;

// The library renders string tooltips as HTML, and titles come from ingested text.
const escapeHtml = (value: string): string =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

export default function NeighborhoodGraph({
  graph,
  overlayRef,
  onExpandNode,
  onOpenNode,
  onOpenEdge,
}: NeighborhoodGraphProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const forceGraphRef = useRef<
    | ForceGraphMethods<NodeObject<GraphNode>, LinkObject<GraphNode, GraphLink>>
    | undefined
  >(undefined);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  // The layout runs twice: once hidden to learn its final extent and fit the view,
  // then again from scratch on screen, so the settling animation never needs a re-fit.
  const [measuring, setMeasuring] = useState(true);
  // Nodes from the on-screen simulation, kept so an expanded graph grows in place.
  const placedNodesRef = useRef(new Map<string, SimulationNode>());
  const pendingClickRef = useRef<{
    selection: MemoryNodeReference;
    timer: number;
  } | null>(null);

  useEffect(
    () => () => window.clearTimeout(pendingClickRef.current?.timer),
    [],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver(() => {
      setSize({
        width: container.clientWidth,
        height: container.clientHeight,
      });
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  // The simulation mutates its nodes and links, so each pass gets its own copies.
  const graphData = useMemo(() => {
    const nodesById = new Map(graph.nodes.map((node) => [node.node_id, node]));
    const edges = graph.edges.filter(
      (edge) =>
        nodesById.has(edge.from_node_id) && nodesById.has(edge.to_node_id),
    );

    const pairKey = (a: string, b: string) =>
      a < b ? `${a}|${b}` : `${b}|${a}`;
    const pairCounts = new Map<string, number>();
    for (const edge of edges) {
      const key = pairKey(edge.from_node_id, edge.to_node_id);
      pairCounts.set(key, (pairCounts.get(key) ?? 0) + 1);
    }

    const pairSeen = new Map<string, number>();
    const links: GraphLink[] = edges.map((edge) => {
      const key = pairKey(edge.from_node_id, edge.to_node_id);
      const count = pairCounts.get(key) ?? 1;
      const index = pairSeen.get(key) ?? 0;
      pairSeen.set(key, index + 1);

      // Fan out parallel edges so each one can be hovered separately.
      const direction = edge.from_node_id < edge.to_node_id ? 1 : -1;
      const curvature =
        edge.from_node_id === edge.to_node_id
          ? 0.6 + index * 0.25
          : (index - (count - 1) / 2) * 0.35 * direction;

      const fromTitle = nodesById.get(edge.from_node_id)?.title ?? "";
      const toTitle = nodesById.get(edge.to_node_id)?.title ?? "";

      return {
        source: edge.from_node_id,
        target: edge.to_node_id,
        edge_id: edge.edge_id,
        label:
          `${fromTitle} ${edge.relation_type.replaceAll("_", " ")} ${toTitle}` +
          (edge.details ? ` — ${edge.details}` : ""),
        curvature,
      };
    });

    const placedNodes = placedNodesRef.current;
    const nodes: SimulationNode[] = graph.nodes.map(
      (node) => placedNodes.get(node.node_id) ?? { ...node, id: node.node_id },
    );

    // Nodes added by an expansion start next to the placed node they connect to.
    if (placedNodes.size > 0) {
      const simulationNodesById = new Map(
        nodes.map((node) => [node.node_id, node]),
      );
      let added = 0;
      for (const edge of edges) {
        for (const [newId, anchorId] of [
          [edge.from_node_id, edge.to_node_id],
          [edge.to_node_id, edge.from_node_id],
        ]) {
          const node = simulationNodesById.get(newId);
          const anchor = placedNodes.get(anchorId);
          if (!node || node.x !== undefined || anchor?.x === undefined) {
            continue;
          }
          const angle = added * 2.4;
          node.x = anchor.x + Math.cos(angle) * 12;
          node.y = (anchor.y ?? 0) + Math.sin(angle) * 12;
          added += 1;
        }
      }
    }

    return { nodes, links };
  }, [graph, measuring]);

  useEffect(() => {
    placedNodesRef.current = new Map(
      measuring ? [] : graphData.nodes.map((node) => [node.node_id, node]),
    );
  }, [graphData, measuring]);

  useEffect(() => {
    setHoveredEdgeId(null);
    setHoveredNodeId(null);
  }, [graphData]);

  // Width of the canvas's left edge currently covered by the overlay, if any.
  const overlayInset = (): number => {
    const container = containerRef.current;
    const overlay = overlayRef?.current;
    if (!container || !overlay) return 0;

    const containerRect = container.getBoundingClientRect();
    const overlayRect = overlay.getBoundingClientRect();
    const overlapsVertically =
      overlayRect.bottom > containerRect.top &&
      overlayRect.top < containerRect.bottom;
    if (!overlapsVertically) return 0;

    return Math.max(
      0,
      Math.min(overlayRect.right, containerRect.right) - containerRect.left,
    );
  };

  const fitToVisibleArea = () => {
    const forceGraph = forceGraphRef.current;
    const bbox = forceGraph?.getGraphBbox();
    if (!forceGraph || !bbox) return;

    const inset = overlayInset();
    const availableWidth = Math.max(size.width - inset - FIT_PADDING * 2, 1);
    const availableHeight = Math.max(size.height - FIT_PADDING * 2, 1);
    const graphWidth = Math.max(bbox.x[1] - bbox.x[0], 1);
    const graphHeight = Math.max(bbox.y[1] - bbox.y[0], 1);
    const zoom = Math.min(
      availableWidth / graphWidth,
      availableHeight / graphHeight,
      MAX_FIT_ZOOM,
    );

    // Shift the view so the graph is centred in the part the overlay leaves free.
    forceGraph.zoom(zoom, 0);
    forceGraph.centerAt(
      (bbox.x[0] + bbox.x[1]) / 2 - inset / 2 / zoom,
      (bbox.y[0] + bbox.y[1]) / 2,
      0,
    );
  };

  return (
    <div
      ref={containerRef}
      className="graph-canvas"
      role="img"
      aria-label={`Graph of ${graphData.nodes.length} nodes and ${graphData.links.length} relationships`}
    >
      {size.width > 0 && (
        <div style={{ visibility: measuring ? "hidden" : "visible" }}>
          <ForceGraph2D<GraphNode, GraphLink>
            ref={forceGraphRef}
            width={size.width}
            height={size.height}
            graphData={graphData}
            warmupTicks={measuring ? LAYOUT_TICKS : 0}
            cooldownTicks={measuring ? 0 : LAYOUT_TICKS}
            onEngineStop={() => {
              if (!measuring) return;
              fitToVisibleArea();
              setMeasuring(false);
            }}
            nodeLabel={() => ""}
            nodeVal={NODE_RADIUS ** 2}
            nodeRelSize={1}
            nodeCanvasObject={(node, ctx, globalScale) => {
              const x = node.x ?? 0;
              const y = node.y ?? 0;
              const isStart = node.node_id === graph.start_node_id;
              const isHovered = node.node_id === hoveredNodeId;

              ctx.beginPath();
              ctx.arc(x, y, NODE_RADIUS, 0, 2 * Math.PI);
              ctx.fillStyle = isStart || isHovered ? "#282723" : "#8c8981";
              ctx.fill();

              // Keep labels the same size on screen regardless of zoom.
              const fontSize = 12 / globalScale;
              ctx.font = `${fontSize}px ${LABEL_FONT}`;
              ctx.textAlign = "center";
              ctx.textBaseline = "top";
              ctx.fillStyle = isStart || isHovered ? "#282723" : "#68665f";
              ctx.fillText(node.title, x, y + NODE_RADIUS + 4 / globalScale);
            }}
            nodePointerAreaPaint={(node, color, ctx) => {
              ctx.beginPath();
              ctx.arc(
                node.x ?? 0,
                node.y ?? 0,
                NODE_RADIUS + 3,
                0,
                2 * Math.PI,
              );
              ctx.fillStyle = color;
              ctx.fill();
            }}
            onNodeHover={(node) => setHoveredNodeId(node?.node_id ?? null)}
            onNodeClick={(node) => {
              const selection = {
                node_id: node.node_id,
                node_type: node.node_type,
                title: node.title,
              };
              const pending = pendingClickRef.current;
              pendingClickRef.current = null;
              window.clearTimeout(pending?.timer);

              // A second click on the same node opens it; otherwise a click expands.
              if (pending?.selection.node_id === node.node_id) {
                if (node.node_id !== graph.start_node_id) onOpenNode(selection);
                return;
              }
              if (pending) onExpandNode(pending.selection);

              pendingClickRef.current = {
                selection,
                timer: window.setTimeout(() => {
                  pendingClickRef.current = null;
                  onExpandNode(selection);
                }, DOUBLE_CLICK_MS),
              };
            }}
            linkLabel={(link) => escapeHtml(link.label)}
            linkCurvature={(link) => link.curvature}
            linkColor={(link) =>
              link.edge_id === hoveredEdgeId ? "#282723" : "#d8d5ce"
            }
            linkWidth={(link) => (link.edge_id === hoveredEdgeId ? 2 : 1)}
            linkHoverPrecision={8}
            onLinkHover={(link) => setHoveredEdgeId(link?.edge_id ?? null)}
            onLinkClick={(link) => onOpenEdge(link.edge_id)}
          />
        </div>
      )}
    </div>
  );
}
