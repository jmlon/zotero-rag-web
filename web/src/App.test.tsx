import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("shows the server's health once it answers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ database: "ok", version: "0.1.0" }))),
    );
    render(<App />);
    expect(screen.getByRole("heading", { name: "Zotero RAG" })).toBeInTheDocument();
    expect(await screen.findByText(/database are up/)).toBeInTheDocument();
  });
});
