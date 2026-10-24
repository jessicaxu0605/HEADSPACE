import type {
  MemoryEdgeDetail,
  MemoryGraphNeighborhood,
  MemoryLanding,
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
  match_kind:
    | "external_id"
    | "identifier"
    | "normalized_title"
    | "fuzzy"
    | "identity"
    | "profile";
  match_score: number;
  reasons: string[];
};

export type NodeQueryResponse = {
  query: string;
  node_type: MemoryNodeType | null;
  matches: NodeQueryMatch[];
};

export type NodeNeighborhoodResponse = MemoryGraphNeighborhood;

export type UserProfile = {
  id: string;
  email: string;
  name: string;
  birthday: string | null;
  about: string | null;
  created_at: string;
};

export type AuthSession = {
  id: string;
  user: UserProfile;
  created_at: string;
  expires_at: string;
};

export type LoginResponse = {
  access_token: string;
  token_type: "bearer";
  session: AuthSession;
};

export type WorkspaceRole = "owner" | "editor" | "viewer";

export type Workspace = {
  id: string;
  name: string;
  created_by_user_id: string;
  role: WorkspaceRole;
  created_at: string;
  // The latest change to the workspace's memory nodes or edges.
  last_edited_at: string;
};

export type CreateUserInput = {
  email: string;
  password: string;
  firstName: string;
  lastName: string;
  about?: string;
};

type ApiErrorBody = {
  detail?: string | Array<{ msg?: string }>;
  error?: Record<string, unknown> | null;
};

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL;

if (!configuredApiBaseUrl) {
  throw new Error("VITE_API_BASE_URL is not configured.");
}

const apiBaseUrl = configuredApiBaseUrl.replace(/\/$/, "");
const accessTokenKey = "headspace.access-token";

// The stored token is the single source of truth for whether this browser is
// signed in. React subscribes to it (see SessionProvider), so clearing it here
// after a 401 signs the reader out everywhere, including other tabs.
const accessTokenListeners = new Set<() => void>();

const notifyAccessTokenListeners = (): void => {
  for (const listener of accessTokenListeners) listener();
};

export const subscribeAccessToken = (listener: () => void): (() => void) => {
  const handleStorage = (event: StorageEvent): void => {
    if (event.key === accessTokenKey || event.key === null) listener();
  };
  accessTokenListeners.add(listener);
  window.addEventListener("storage", handleStorage);
  return () => {
    accessTokenListeners.delete(listener);
    window.removeEventListener("storage", handleStorage);
  };
};

export const getAccessToken = (): string | null =>
  window.localStorage.getItem(accessTokenKey);

export const setAccessToken = (token: string): void => {
  window.localStorage.setItem(accessTokenKey, token);
  notifyAccessTokenListeners();
};

export const clearAccessToken = (): void => {
  if (getAccessToken() === null) return;
  window.localStorage.removeItem(accessTokenKey);
  notifyAccessTokenListeners();
};

// A 403 on a workspace-scoped request means the reader lost access to that
// workspace; WorkspaceProvider listens and re-checks their memberships.
const workspaceAccessDeniedListeners = new Set<(workspaceId: string) => void>();

export const subscribeWorkspaceAccessDenied = (
  listener: (workspaceId: string) => void,
): (() => void) => {
  workspaceAccessDeniedListeners.add(listener);
  return () => workspaceAccessDeniedListeners.delete(listener);
};

const errorMessage = (payload: unknown, response: Response): string => {
  if (payload && typeof payload === "object") {
    const apiError = payload as ApiErrorBody;
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
  const headers = new Headers(init?.headers);
  const accessToken = getAccessToken();
  if (accessToken && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  const response = await fetch(`${apiBaseUrl}${path}`, { ...init, headers });
  const payload = (await response.json().catch(() => null)) as unknown;

  if (!response.ok) {
    // Only a rejected token ends the session; a failed login also returns 401.
    if (response.status === 401 && accessToken !== null) {
      if (getAccessToken() === accessToken) clearAccessToken();
    }
    throw new ApiError(errorMessage(payload, response), response.status);
  }

  return payload as ResponseBody;
};

// Every graph read and write is scoped to one workspace the reader belongs to.
const requestWorkspaceJson = async <ResponseBody>(
  workspaceId: string,
  path: string,
  init?: RequestInit,
): Promise<ResponseBody> => {
  const separator = path.includes("?") ? "&" : "?";
  const scopedPath = `${path}${separator}workspace_id=${encodeURIComponent(workspaceId)}`;
  try {
    return await requestJson<ResponseBody>(scopedPath, init);
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) {
      for (const listener of workspaceAccessDeniedListeners) {
        listener(workspaceId);
      }
    }
    throw error;
  }
};

export const createUser = async ({
  email,
  password,
  firstName,
  lastName,
  about,
}: CreateUserInput): Promise<UserProfile> =>
  requestJson<UserProfile>("/api/v1/auth/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: email.trim().toLowerCase(),
      password,
      name: `${firstName.trim()} ${lastName.trim()}`.trim(),
      about: about?.trim() || null,
    }),
  });

export const login = async (
  email: string,
  password: string,
): Promise<LoginResponse> =>
  requestJson<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: email.trim().toLowerCase(),
      password,
    }),
  });

export const getCurrentSession = async (
  options: { signal?: AbortSignal } = {},
): Promise<AuthSession> =>
  requestJson<AuthSession>("/api/v1/auth/session", { signal: options.signal });

export const logout = async (): Promise<void> => {
  await requestJson<null>("/api/v1/auth/logout", { method: "POST" });
};

export type WorkspaceList = {
  workspaces: Workspace[];
  last_used_workspace_id: string | null;
};

export const listWorkspaces = async (
  options: { signal?: AbortSignal } = {},
): Promise<WorkspaceList> => {
  const payload = await requestJson<WorkspaceList>("/api/v1/workspaces", {
    signal: options.signal,
  });

  if (!payload || !Array.isArray(payload.workspaces)) {
    throw new Error("The API returned an unexpected response.");
  }

  return payload;
};

// Remembers the workspace on the server, so the next visit on any device
// opens it without asking.
export const markWorkspaceLastUsed = async (workspaceId: string): Promise<void> => {
  await requestJson<null>(
    `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/last-used`,
    { method: "PUT" },
  );
};

export const createManualIngestion = async (
  workspaceId: string,
  content: string,
  clientRequestId: string,
): Promise<IngestionRun> =>
  requestWorkspaceJson<IngestionRun>(workspaceId, "/api/v1/ingestions", {
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
  workspaceId: string,
  query: string,
  options: { limit?: number; signal?: AbortSignal } = {},
): Promise<NodeQueryResponse> => {
  const payload = await requestWorkspaceJson<NodeQueryResponse>(
    workspaceId,
    "/api/v1/nodes/query",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit: options.limit ?? 5 }),
      signal: options.signal,
    },
  );

  if (!payload || !Array.isArray(payload.matches)) {
    throw new Error("The API returned an unexpected response.");
  }

  return payload;
};

export const getNodeNeighborhood = async (
  workspaceId: string,
  nodeId: string,
  options: { depth?: number; signal?: AbortSignal } = {},
): Promise<NodeNeighborhoodResponse> => {
  const depth = options.depth ?? 1;
  const payload = await requestWorkspaceJson<NodeNeighborhoodResponse>(
    workspaceId,
    `/api/v1/nodes/${encodeURIComponent(nodeId)}/graph?depth=${depth}`,
    { signal: options.signal },
  );

  if (
    !payload ||
    !Array.isArray(payload.nodes) ||
    !Array.isArray(payload.edges)
  ) {
    throw new Error("The API returned an unexpected response.");
  }

  return payload;
};

export const getIngestionGraph = async (
  workspaceId: string,
  runId: string,
  options: { signal?: AbortSignal } = {},
): Promise<MemoryRunGraph> => {
  const payload = await requestWorkspaceJson<MemoryRunGraph>(
    workspaceId,
    `/api/v1/ingestions/${encodeURIComponent(runId)}/graph`,
    { signal: options.signal },
  );

  if (
    !payload ||
    !Array.isArray(payload.nodes) ||
    !Array.isArray(payload.edges)
  ) {
    throw new Error("The API returned an unexpected response.");
  }

  return payload;
};

export const getEdgeDetail = async (
  workspaceId: string,
  edgeId: string,
  options: { signal?: AbortSignal } = {},
): Promise<MemoryEdgeDetail> => {
  const payload = await requestWorkspaceJson<MemoryEdgeDetail>(
    workspaceId,
    `/api/v1/edges/${encodeURIComponent(edgeId)}`,
    { signal: options.signal },
  );

  if (!payload || !payload.from_node || !Array.isArray(payload.evidence)) {
    throw new Error("The API returned an unexpected response.");
  }

  return payload;
};

export const getLandingMemories = async (
  workspaceId: string,
  options: {
    maxCards?: number;
    // Recently shown IDs are left out of the draw; open tasks always return.
    excludeNodeIds?: string[];
    excludeEdgeIds?: string[];
    signal?: AbortSignal;
  } = {},
): Promise<MemoryLanding> => {
  const params = new URLSearchParams({
    max_cards: String(options.maxCards ?? 6),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  });
  for (const nodeId of options.excludeNodeIds ?? []) {
    params.append("exclude_node_ids", nodeId);
  }
  for (const edgeId of options.excludeEdgeIds ?? []) {
    params.append("exclude_edge_ids", edgeId);
  }

  const payload = await requestWorkspaceJson<MemoryLanding>(
    workspaceId,
    `/api/v1/landing?${params.toString()}`,
    { signal: options.signal },
  );

  if (
    !payload ||
    !Array.isArray(payload.upcoming) ||
    !Array.isArray(payload.memories)
  ) {
    throw new Error("The API returned an unexpected response.");
  }

  return payload;
};
