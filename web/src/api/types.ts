/** The JSON API's shapes (src/zotero_rag_web/api/*, PRD §6.3). */

export interface Library {
  name: string | null;
  web_url: string | null;
  /** The group's library on zotero.org. */
  library_url: string | null;
  items: number;
  chunks: number;
  indexed: boolean;
  year_min: number | null;
  year_max: number | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
}

export interface Collection {
  key: string;
  parent_key: string | null;
  name: string;
  path: string;
  item_count: number;
}

export interface Tag {
  tag: string;
  items: number;
}

export interface Filters {
  collection?: string;
  year_min?: number;
  year_max?: number;
  include_undated?: boolean;
  tags?: string[];
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Source {
  number: number;
  item_key: string;
  title: string;
  authors: string;
  year: string;
  zotero_url: string;
  locator: string;
  cited: boolean;
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  total_cost: number;
  elapsed_s: number;
  cost_source?: string;
}

export type MessageStatus = "done" | "streaming" | "stopped" | "error";

export interface Message {
  id: string;
  role: "user" | "assistant" | "event";
  content: string;
  standalone_query: string | null;
  sources: Source[] | null;
  usage: Usage | null;
  status: MessageStatus;
  created_at: string;
}

export interface Conversation extends ConversationSummary {
  filters: Filters;
  messages: Message[];
}

/** What a stream ends with. */
export interface Done {
  message_id: string;
  status: "done" | "stopped";
  answer: string;
  sources: Source[];
  usage: Usage;
  standalone: string | null;
}

export type Stage = "condensing" | "retrieving" | "answering";

/** Codes the server sends (PRD §6.4), translated in the locales under `errors.`. */
export type ErrorCode =
  | "rate_limited"
  | "budget_exhausted"
  | "llm_failed"
  | "index_empty"
  | "index_mismatch"
  | "question_too_long"
  | "conversation_full"
  | "unknown_collection"
  | "not_configured"
  | "not_found"
  | "invalid_request"
  | "internal"
  | "network";
