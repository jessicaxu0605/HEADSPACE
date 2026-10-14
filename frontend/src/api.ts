import type {
  MemoryEdgeDetail,
  MemoryGraphNeighborhood,
  MemoryNodeReference,
  MemoryNodeType,
  MemoryRunGraph,
} from "./memory_graph";

export type IngestionRun = {
  id: string;
  status: "pending" | "processing" | "completed" | "failed";
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
};

export type NodeQueryMatch = MemoryNodeReference & {
  identifiers: string[];
  match_kind: "external_id" | "identifier" | "normalized_title" | "fuzzy";
  match_score: number;
  reasons: string[];
};

export type NodeQueryResponse = {
  query: string;
  node_type: MemoryNodeType | null;
  matches: NodeQueryMatch[];
};

export type NodeNeighborhoodResponse = MemoryGraphNeighborhood;

type ApiError = {
  detail?: string | Array<{ msg?: string }>;
  error?: Record<string, unknown> | null;
};

const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL;

if (!configuredApiBaseUrl) {
  throw new Error("VITE_API_BASE_URL is not configured.");
}

const apiBaseUrl = configuredApiBaseUrl.replace(/\/$/, "");

const errorMessage = (payload: unknown, response: Response): string => {
  if (payload && typeof payload === "object") {
    const apiError = payload as ApiError;
    if (typeof apiError.detail === "string") {
      return apiError.detail;
    }
    if (Array.isArray(apiError.detail)) {
      return apiError.detail
        .map((item) => item.msg)
        .filter(Boolean)
        .join(", ");
    }
    if (apiError.error) {
      return JSON.stringify(apiError.error);
    }
  }

  return `The API returned ${response.status} ${response.statusText}.`;
};

const requestJson = async <ResponseBody>(
  path: string,
  init?: RequestInit,
): Promise<ResponseBody> => {
  const response = await fetch(`${apiBaseUrl}${path}`, init);
  const payload = (await response.json().catch(() => null)) as unknown;

  if (!response.ok) {
    throw new Error(errorMessage(payload, response));
  }

  return payload as ResponseBody;
};

export const createManualIngestion = async (
  content: string,
  clientRequestId: string,
): Promise<IngestionRun> =>
  requestJson<IngestionRun>("/api/v1/ingestions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_type: "manual_entry",
      content,
      source_metadata: {},
      client_request_id: clientRequestId,
    }),
  });

export const queryNodes = async (
  query: string,
  options: { limit?: number; signal?: AbortSignal } = {},
): Promise<NodeQueryResponse> => {
  const payload = await requestJson<NodeQueryResponse>("/api/v1/nodes/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, limit: options.limit ?? 5 }),
    signal: options.signal,
  });

  if (!payload || !Array.isArray(payload.matches)) {
    throw new Error("The API returned an unexpected response.");
  }

  return payload;
};

export const getNodeNeighborhood = async (
  nodeId: string,
  options: { depth?: number; signal?: AbortSignal } = {},
): Promise<NodeNeighborhoodResponse> => {
  const depth = options.depth ?? 2;
  const payload = await requestJson<NodeNeighborhoodResponse>(
    `/api/v1/nodes/${encodeURIComponent(nodeId)}/graph?depth=${depth}`,
    { signal: options.signal },
  );

  if (!payload || !Array.isArray(payload.nodes) || !Array.isArray(payload.edges)) {
    throw new Error("The API returned an unexpected response.");
  }

  return payload;
};

export const getIngestionGraph = async (
  runId: string,
  options: { signal?: AbortSignal } = {},
): Promise<MemoryRunGraph> => {
  const payload = await requestJson<MemoryRunGraph>(
    `/api/v1/ingestions/${encodeURIComponent(runId)}/graph`,
    { signal: options.signal },
  );

  if (!payload || !Array.isArray(payload.nodes) || !Array.isArray(payload.edges)) {
    throw new Error("The API returned an unexpected response.");
  }

  return payload;
};

export const getEdgeDetail = async (
  edgeId: string,
  options: { signal?: AbortSignal } = {},
): Promise<MemoryEdgeDetail> => {
  const payload = await requestJson<MemoryEdgeDetail>(
    `/api/v1/edges/${encodeURIComponent(edgeId)}`,
    { signal: options.signal },
  );

  if (!payload || !payload.from_node || !Array.isArray(payload.evidence)) {
    throw new Error("The API returned an unexpected response.");
  }

  return payload;
};
