/** Server-Sent Events from a fetch body. `EventSource` cannot POST, so this reads
 * the stream itself: events are separated by a blank line, `:` lines are comments
 * (the server's keep-alive), and an event may arrive split across chunks. */

export interface ServerEvent {
  event: string;
  data: unknown;
}

export function createParser(onEvent: (event: ServerEvent) => void): (chunk: string) => void {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk.replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let name = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue;
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) onEvent({ event: name, data: JSON.parse(data.join("\n")) });
      boundary = buffer.indexOf("\n\n");
    }
  };
}

export async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ServerEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parse = createParser(onEvent);
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      parse(decoder.decode());
      return;
    }
    // `stream: true` keeps a multi-byte character split across chunks intact.
    parse(decoder.decode(value, { stream: true }));
  }
}
