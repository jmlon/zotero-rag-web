import { describe, expect, it } from "vitest";

import { createParser, readEvents, type ServerEvent } from "./sse";

function collect(chunks: string[]): ServerEvent[] {
  const events: ServerEvent[] = [];
  const parse = createParser((event) => events.push(event));
  chunks.forEach(parse);
  return events;
}

describe("the SSE parser", () => {
  it("reads named events with JSON data", () => {
    expect(collect(['event: token\ndata: {"text":"Hi"}\n\n'])).toEqual([
      { event: "token", data: { text: "Hi" } },
    ]);
  });

  it("joins an event split across chunks, even mid-line", () => {
    const events = collect(['event: tok', 'en\ndata: {"te', 'xt":"a"}\n', '\nevent: token\ndata: {"text":"b"}\n\n']);
    expect(events.map((e) => (e.data as { text: string }).text)).toEqual(["a", "b"]);
  });

  it("skips keep-alive comments", () => {
    expect(collect([": keep-alive\n\n", 'event: done\ndata: {"status":"done"}\n\n'])).toHaveLength(1);
  });

  it("reads a fetch body to the end", async () => {
    const body = new Response('event: status\ndata: {"stage":"answering"}\n\nevent: token\ndata: {"text":"x"}\n\n').body!;
    const events: ServerEvent[] = [];
    await readEvents(body, (event) => events.push(event));
    expect(events.map((e) => e.event)).toEqual(["status", "token"]);
  });
});
