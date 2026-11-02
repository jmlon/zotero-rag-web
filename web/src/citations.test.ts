import { describe, expect, it } from "vitest";

import type { Source } from "./api/types";
import { answerMarkdown, citedNumbers, linkCitations } from "./citations";

const source: Source = {
  number: 1, item_key: "P1", title: "What is a Fog Node", authors: "Tordera, Eva",
  year: "2016", zotero_url: "https://www.zotero.org/groups/1/items/P1", locator: "p. 3", cited: true,
};

describe("citations", () => {
  it("turns markers into cite links, one per number", () => {
    expect(linkCitations("Fog nodes [1]. Both [2, 3].")).toBe(
      "Fog nodes [1](cite:1). Both [2](cite:2)[3](cite:3).",
    );
  });

  it("leaves Markdown links and plain brackets alone", () => {
    expect(linkCitations("See [the paper](https://x.org) and [a] note.")).toBe(
      "See [the paper](https://x.org) and [a] note.",
    );
  });

  it("lists the numbers an answer cites", () => {
    expect(citedNumbers("A [2]. B [1, 2]. C.")).toEqual([1, 2]);
  });

  it("copies an answer with its sources", () => {
    expect(answerMarkdown("Fog nodes [1].", [source], "Sources")).toBe(
      "Fog nodes [1].\n\n**Sources**\n\n- [1] What is a Fog Node — Tordera, Eva (2016), p. 3\n" +
        "  https://www.zotero.org/groups/1/items/P1\n",
    );
  });
});
