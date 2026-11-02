import { describe, expect, it } from "vitest";

import { fetchHealth } from "./health";

function respond(status: number, body: object): typeof fetch {
  return async () => new Response(JSON.stringify(body), { status });
}

describe("fetchHealth", () => {
  it("is ok when the server and the database are up", async () => {
    const health = await fetchHealth(respond(200, { database: "ok", version: "0.1.0" }));
    expect(health).toEqual({ state: "ok", version: "0.1.0" });
  });

  it("is degraded when the database is unreachable", async () => {
    const health = await fetchHealth(respond(503, { database: "unreachable", version: "0.1.0" }));
    expect(health).toEqual({ state: "degraded", database: "unreachable", version: "0.1.0" });
  });

  it("is offline when the request fails", async () => {
    const health = await fetchHealth(async () => {
      throw new TypeError("network");
    });
    expect(health).toEqual({ state: "offline" });
  });
});
