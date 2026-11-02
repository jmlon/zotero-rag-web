import type { Source } from "./api/types";

/** `[1]` or `[2, 3]`, but not the text of a Markdown link `[x](…)`. */
const MARKER = /\[(\d+(?:\s*,\s*\d+)*)\](?!\()/g;
export const CITE_SCHEME = "cite:";

/** The answer's Markdown, each citation number turned into a `cite:` link, which
 * the renderer draws as a chip. `[2, 3]` becomes two chips. */
export function linkCitations(answer: string): string {
  return answer.replace(MARKER, (_, numbers: string) =>
    numbers
      .split(",")
      .map((n) => `[${n.trim()}](${CITE_SCHEME}${n.trim()})`)
      .join(""),
  );
}

export function citedNumbers(answer: string): number[] {
  const found = new Set<number>();
  for (const match of answer.matchAll(MARKER)) {
    for (const n of match[1].split(",")) found.add(Number(n.trim()));
  }
  return [...found].sort((a, b) => a - b);
}

export function sourceLabel(source: Source): string {
  let label = source.title;
  if (source.authors) label += ` — ${source.authors}`;
  if (source.year) label += ` (${source.year})`;
  return `${label}, ${source.locator}`;
}

/** An answer with its sources as Markdown, for the clipboard: the format of the
 * CLI terminal UI's copy (zotero_rag/tui/export.py). */
export function answerMarkdown(answer: string, sources: Source[], heading: string): string {
  if (!sources.length) return answer.trim() + "\n";
  const lines = sources.map(
    (s) => `- [${s.number}] ${sourceLabel(s)}` + (s.zotero_url ? `\n  ${s.zotero_url}` : ""),
  );
  return `${answer.trim()}\n\n**${heading}**\n\n${lines.join("\n")}\n`;
}
