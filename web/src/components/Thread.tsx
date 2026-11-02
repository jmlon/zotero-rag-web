import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Message, Source } from "../api/types";
import { answerMarkdown, sourceLabel } from "../citations";
import type { Pending } from "../hooks/useAsk";
import { AnswerMarkdown } from "./AnswerMarkdown";
import { ErrorNote } from "./ErrorNote";

interface ThreadProps {
  messages: Message[];
  pending: Pending | null;
  filtered: boolean;
  onRetry: (question: string) => void;
  onSearchEverything: (question: string) => void;
}

/** The conversation: saved messages, then the answer streaming in, if any. */
export function Thread({ messages, pending, filtered, onRetry, onSearchEverything }: ThreadProps) {
  const { t } = useTranslation();
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, pending?.text, pending?.status]);

  const lastQuestion = [...messages].reverse().find((m) => m.role === "user")?.content ?? "";
  const lastAnswer = [...messages].reverse().find((m) => m.role === "assistant");

  // One flat keyed array (not a .map() beside separate elements, whose keys React
  // would match separately): once the stream ends, the saved answer takes the
  // streamed one's place under the same key, so its state (an open card) survives.
  const items = messages.map((message) => {
    if (message.role === "event") return <FiltersChanged key={message.id} />;
    if (message.role === "user") return <Question key={message.id} text={message.content} />;
    const isLast = message.id === lastAnswer?.id && !pending;
    return (
      <Answer
        key={message.id}
        text={message.content}
        sources={message.sources ?? []}
        standalone={message.standalone_query}
        status={message.status}
        onRetry={isLast ? () => onRetry(lastQuestion) : undefined}
        onSearchEverything={
          isLast && filtered && message.status === "done" && !message.sources?.length
            ? () => onSearchEverything(lastQuestion)
            : undefined
        }
      />
    );
  });
  if (pending) {
    items.push(<Question key="pending-question" text={pending.question} />);
    items.push(
      pending.status === "error" ? (
        <ErrorNote
          key="pending-error"
          code={pending.error ?? "internal"}
          retryAfterS={pending.retryAfterS}
          onRetry={() => onRetry(pending.question)}
        />
      ) : (
        <Answer
          key={pending.messageId ?? "pending-answer"}
          text={pending.text}
          sources={pending.sources}
          standalone={pending.standalone}
          status={pending.status}
          stage={pending.stage ? t(`stage.${pending.stage}`) : null}
        />
      ),
    );
  }

  return (
    <div className="thread" aria-live="polite">
      {items}
      <div ref={end} />
    </div>
  );
}

function Question({ text }: { text: string }) {
  return <div className="message question">{text}</div>;
}

function FiltersChanged() {
  const { t } = useTranslation();
  return (
    <div className="divider" role="separator">
      <span>{t("filters.changed")}</span>
    </div>
  );
}

interface AnswerProps {
  text: string;
  sources: Source[];
  standalone: string | null;
  status: Message["status"];
  stage?: string | null;
  onRetry?: () => void;
  onSearchEverything?: () => void;
}

function Answer({ text, sources, standalone, status, stage, onRetry, onSearchEverything }: AnswerProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(answerMarkdown(text, sources, t("answer.sources")));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard blocked (insecure origin, permissions): nothing to do.
    }
  };

  return (
    <article className={`message answer status-${status}`}>
      {standalone && (
        <p className="searched-for">{t("answer.searchedFor", { query: standalone })}</p>
      )}
      {stage && !text && (
        <p className="stage" role="status">
          {stage}
        </p>
      )}
      {status === "error" && !text ? (
        <p className="error-text">{t("answer.failed")}</p>
      ) : status === "streaming" && !text && !stage ? (
        <p className="muted">{t("answer.inProgress")}</p>
      ) : (
        <AnswerMarkdown text={text} sources={sources} />
      )}
      {status === "streaming" && text && <span className="cursor" aria-hidden="true" />}
      {status === "stopped" && <p className="muted">{t("answer.stopped")}</p>}
      {sources.length > 0 && (
        <section className="sources" aria-label={t("answer.sources")}>
          <h3>{t("answer.sources")}</h3>
          <ol>
            {sources.map((source) => (
              <li key={source.number} value={source.number}>
                {source.zotero_url ? (
                  <a href={source.zotero_url} target="_blank" rel="noopener noreferrer">
                    {sourceLabel(source)}
                  </a>
                ) : (
                  sourceLabel(source)
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
      {onSearchEverything && (
        <p className="no-results">
          {t("answer.noResultsFiltered")}{" "}
          <button type="button" className="link-button" onClick={onSearchEverything}>
            {t("answer.searchEverything")}
          </button>
        </p>
      )}
      {status !== "streaming" && (
        <div className="answer-actions">
          {text && (
            <button type="button" className="ghost" onClick={copy}>
              {copied ? t("answer.copied") : t("answer.copy")}
            </button>
          )}
          {onRetry && (status === "error" || status === "stopped") && (
            <button type="button" className="ghost" onClick={onRetry}>
              {t("answer.retry")}
            </button>
          )}
        </div>
      )}
    </article>
  );
}
