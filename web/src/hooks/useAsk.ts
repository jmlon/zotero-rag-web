import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";

import { ApiError, api, errorFrom } from "../api/client";
import { readEvents } from "../api/sse";
import type { Done, ErrorCode, Source, Stage } from "../api/types";
import { keys } from "./queries";

const SETTLE_ATTEMPTS = 20;
const SETTLE_INTERVAL_MS = 250;

/** The question being answered right now, as it streams in. */
export interface Pending {
  conversationId: string;
  question: string;
  stage: Stage | null;
  standalone: string | null;
  text: string;
  sources: Source[];
  status: "streaming" | "done" | "stopped" | "error";
  /** The saved answer's id, once `done` names it: the thread keys the streamed
   * answer by it, so React keeps the same component when the saved one replaces it. */
  messageId?: string;
  error?: ErrorCode;
  retryAfterS?: number;
}

/** Ask, stream, stop. `pending` stays until the saved conversation is reloaded, so
 * the answer never flickers between the streamed copy and the stored one. A refusal
 * before the stream (a limit, an empty index) saves nothing, so it stays `pending`
 * as an error until dismissed; anything after that is in the saved conversation. */
export function useAsk() {
  const client = useQueryClient();
  const [pending, setPending] = useState<Pending | null>(null);
  const controller = useRef<AbortController | null>(null);

  /** Reload until the server has saved the answer as final, then drop `pending`.
   * After Stop, the server saves the partial answer only when its worker next
   * hears from the model, which can lag the disconnect by a moment. */
  const settle = useCallback(
    async (conversationId: string) => {
      void client.invalidateQueries({ queryKey: keys.conversations });
      for (let attempt = 0; attempt < SETTLE_ATTEMPTS; attempt += 1) {
        const saved = await client.fetchQuery({
          queryKey: keys.conversation(conversationId),
          queryFn: () => api.conversation(conversationId),
          staleTime: 0,
        });
        const last = saved.messages.at(-1);
        if (!last || last.role !== "assistant" || last.status !== "streaming") break;
        await new Promise((resolve) => setTimeout(resolve, SETTLE_INTERVAL_MS));
      }
      setPending(null);
    },
    [client],
  );

  const ask = useCallback(
    async (conversationId: string, question: string) => {
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      const update = (change: Partial<Pending>) =>
        setPending((current) => (current ? { ...current, ...change } : current));
      setPending({
        conversationId, question, stage: null, standalone: null, text: "", sources: [],
        status: "streaming",
      });

      try {
        const response = await fetch(`/api/conversations/${conversationId}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({ question }),
          signal: abort.signal,
          credentials: "same-origin",
        });
        if (!response.ok || !response.body) {
          const error = await errorFrom(response);
          update({ status: "error", error: error.code, retryAfterS: error.retryAfterS });
          return;
        }
        await readEvents(response.body, ({ event, data }) => {
          if (event === "status") update({ stage: (data as { stage: Stage }).stage });
          else if (event === "query") update({ standalone: (data as { standalone: string }).standalone });
          else if (event === "token") {
            const text = (data as { text: string }).text;
            setPending((current) => (current ? { ...current, text: current.text + text } : current));
          } else if (event === "done") {
            const done = data as Done;
            update({
              status: done.status,
              text: done.answer,
              sources: done.sources,
              stage: null,
              messageId: done.message_id,
            });
          } else if (event === "error") {
            update({ status: "error", error: (data as { code: ErrorCode }).code, stage: null });
          }
        });
      } catch (error) {
        if (abort.signal.aborted) {
          update({ status: "stopped", stage: null });
        } else {
          update({ status: "error", error: error instanceof ApiError ? error.code : "network" });
          return;
        }
      } finally {
        if (controller.current === abort) controller.current = null;
      }
      await settle(conversationId);
    },
    [settle],
  );

  const stop = useCallback(() => controller.current?.abort(), []);
  const dismiss = useCallback(() => setPending(null), []);

  return { pending, ask, stop, dismiss };
}
