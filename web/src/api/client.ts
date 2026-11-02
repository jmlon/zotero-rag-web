import type { Collection, Conversation, ConversationSummary, ErrorCode, Filters, Library, Tag } from "./types";

/** An API refusal, by code; the UI translates the code, never shows server text. */
export class ApiError extends Error {
  constructor(
    readonly code: ErrorCode,
    readonly status: number,
    readonly retryAfterS?: number,
  ) {
    super(code);
  }
}

export async function errorFrom(response: Response): Promise<ApiError> {
  let code: ErrorCode = "internal";
  let retry: number | undefined;
  try {
    const body = (await response.json()) as { error?: { code?: ErrorCode; retry_after_s?: number } };
    code = body.error?.code ?? code;
    retry = body.error?.retry_after_s;
  } catch {
    // Not JSON: a proxy's error page. Keep "internal".
  }
  return new ApiError(code, response.status, retry);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", Accept: "application/json", ...init?.headers },
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError("network", 0);
  }
  if (!response.ok) throw await errorFrom(response);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export const api = {
  library: () => request<Library>("/api/library"),
  collections: () => request<Collection[]>("/api/collections"),
  tags: (q: string) => request<Tag[]>(`/api/tags?q=${encodeURIComponent(q)}`),
  conversations: () => request<ConversationSummary[]>("/api/conversations"),
  conversation: (id: string) => request<Conversation>(`/api/conversations/${id}`),
  createConversation: (filters: Filters) =>
    request<Conversation>("/api/conversations", { method: "POST", body: JSON.stringify({ filters }) }),
  updateConversation: (id: string, change: { title?: string; filters?: Filters }) =>
    request<Conversation>(`/api/conversations/${id}`, { method: "PATCH", body: JSON.stringify(change) }),
  deleteConversation: (id: string) => request<void>(`/api/conversations/${id}`, { method: "DELETE" }),
};
