import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Source } from "../api/types";
import "../i18n";
import { AnswerMarkdown } from "./AnswerMarkdown";

const source: Source = {
  number: 1, item_key: "P1", title: "What is a Fog Node", authors: "Tordera, Eva",
  year: "2016", zotero_url: "https://www.zotero.org/groups/1/items/P1", locator: "p. 3", cited: true,
};

describe("an answer", () => {
  it("draws citations as chips that open their source", async () => {
    render(<AnswerMarkdown text="Fog nodes sit at the edge [1]." sources={[source]} />);
    const chip = screen.getByRole("button", { name: "[1] What is a Fog Node" });
    await userEvent.click(chip);
    const link = await screen.findByRole("link", { name: /Open in Zotero/ });
    expect(link).toHaveAttribute("href", source.zotero_url);
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("Tordera, Eva · 2016 · p. 3")).toBeInTheDocument();
  });

  it("toggles a citation's card with the keyboard", async () => {
    render(<AnswerMarkdown text="Fog [1]." sources={[source]} />);
    screen.getByRole("button", { name: "[1] What is a Fog Node" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("link", { name: /Open in Zotero/ })).toBeInTheDocument();
    screen.getByRole("button", { name: "[1] What is a Fog Node" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.queryByRole("link", { name: /Open in Zotero/ })).toBeNull();
  });

  it("never renders raw HTML or script links from the model", () => {
    const { container } = render(
      <AnswerMarkdown
        text={'<img src=x onerror="alert(1)"> Evil [link](javascript:alert(1)).'}
        sources={[]}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    // The unsafe URL is dropped, leaving the words; nothing points at a script.
    expect(screen.getByText("link")).toBeInTheDocument();
    for (const element of container.querySelectorAll("[href]")) {
      expect(element.getAttribute("href")).not.toMatch(/javascript/i);
    }
  });

  it("shows a citation it has no source for as plain text", () => {
    render(<AnswerMarkdown text="Claim [4]." sources={[source]} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("[4]")).toBeInTheDocument();
  });
});
