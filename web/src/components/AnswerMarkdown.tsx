import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Source } from "../api/types";
import { CITE_SCHEME, linkCitations } from "../citations";
import { CitationChip } from "./CitationChip";

/** Only http(s), mailto and our own `cite:` links survive; raw HTML is never rendered. */
function urlTransform(url: string): string {
  return url.startsWith(CITE_SCHEME) ? url : defaultUrlTransform(url);
}

export function AnswerMarkdown({ text, sources }: { text: string; sources: Source[] }) {
  const byNumber = new Map(sources.map((source) => [source.number, source]));
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={urlTransform}
        components={{
          a({ href, children }) {
            if (href?.startsWith(CITE_SCHEME)) {
              const number = Number(href.slice(CITE_SCHEME.length));
              return <CitationChip number={number} source={byNumber.get(number)} />;
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {linkCitations(text)}
      </ReactMarkdown>
    </div>
  );
}
