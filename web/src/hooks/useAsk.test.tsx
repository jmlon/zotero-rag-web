import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAsk } from "./useAsk";

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function sse(events: [string, object][]): Response {
  const body = events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

afterEach(() => vi.unstubAllGlobals());

const saved = { id: "c1", title: "", filters: {}, created_at: "", updated_at: "", messages: [] };

/** Answers `messages` POSTs with `stream`, and the reload with the saved conversation. */
function routed(stream: (init: RequestInit) => Promise<Response>) {
  return vi.fn(async (url: string, init: RequestInit = {}) =>
    url.endsWith("/messages") ? stream(init) : new Response(JSON.stringify(saved)),
  );
}

describe("useAsk", () => {
  it("collects a streamed answer, then hands over to the saved conversation", async () => {
    const fetcher = routed(async () => sse([
      ["status", { stage: "retrieving" }],
      ["token", { text: "Fog " }],
      ["token", { text: "nodes [1]." }],
      ["done", { status: "done", answer: "Fog nodes [1].", sources: [], usage: {}, standalone: null }],
    ]));
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useAsk(), { wrapper });
    await act(() => result.current.ask("c1", "What is fog?"));
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/conversations/c1/messages");
    expect(JSON.parse(String(init.body))).toEqual({ question: "What is fog?" });
    await waitFor(() => expect(result.current.pending).toBeNull());
  });

  it("keeps a refusal before the stream as an error with its code", async () => {
    vi.stubGlobal("fetch", routed(async () =>
      new Response(JSON.stringify({ error: { code: "rate_limited", retry_after_s: 600 } }), { status: 429 }),
    ));
    const { result } = renderHook(() => useAsk(), { wrapper });
    await act(() => result.current.ask("c1", "Again?"));
    expect(result.current.pending).toMatchObject({ status: "error", error: "rate_limited", retryAfterS: 600 });
  });

  it("stop aborts the request, then reloads the saved answer", async () => {
    // The visible "Stopped." state is covered by the browser test (e2e/chat.spec.ts);
    // inside act() React batches it away, so here the request and the reload are checked.
    let signal: AbortSignal | undefined;
    const fetcher = routed((init) => new Promise((_, reject) => {
      signal = init.signal ?? undefined;
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useAsk(), { wrapper });
    let asking: Promise<void> = Promise.resolve();
    act(() => {
      asking = result.current.ask("c1", "Long question?");
    });
    act(() => result.current.stop());
    await act(() => asking);
    expect(signal?.aborted).toBe(true);
    expect(fetcher.mock.calls.map(([url]) => url)).toContain("/api/conversations/c1");
    expect(result.current.pending).toBeNull();
  });
});
