export type MemoryNodeType =
  | "person"
  | "location"
  | "date"
  | "task"
  | "concept";

export type MemoryNodeReference = {
  node_id: string;
  node_type: MemoryNodeType;
  title: string;
};

export type MemoryNeighborhoodNode = MemoryNodeReference & {
  summary: string | null;
  importance: number;
  confidence: number;
  distance: number;
};

export type MemoryGraphEdge = {
  edge_id: string;
  from_node_id: string;
  to_node_id: string;
  relation_type: string;
  strength: number;
  confidence: number;
};

export type MemoryGraphNeighborhood = {
  start_node_id: string;
  depth: number;
  nodes: MemoryNeighborhoodNode[];
  edges: MemoryGraphEdge[];
};
