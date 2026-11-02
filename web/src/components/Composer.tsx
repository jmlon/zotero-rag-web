import { type FormEvent, type KeyboardEvent, useState } from "react";
import { useTranslation } from "react-i18next";

/** The server's MAX_QUESTION_CHARS default; the server's own check is authoritative. */
export const MAX_QUESTION_CHARS = 2000;

interface ComposerProps {
  streaming: boolean;
  disabled?: boolean;
  onAsk: (question: string) => void;
  onStop: () => void;
}

export function Composer({ streaming, disabled, onAsk, onStop }: ComposerProps) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const over = text.length - MAX_QUESTION_CHARS;
  const canSend = !streaming && !disabled && text.trim().length > 0 && over <= 0;

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!canSend) return;
    onAsk(text.trim());
    setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form className="composer" onSubmit={submit}>
      <label className="visually-hidden" htmlFor="question">
        {t("composer.placeholder")}
      </label>
      <textarea
        id="question"
        rows={2}
        value={text}
        placeholder={t("composer.placeholder")}
        aria-describedby="composer-hint"
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="composer-row">
        <span id="composer-hint" className={over > 0 ? "warning" : "muted"}>
          {over > 0 ? t("composer.tooLong", { count: over }) : t("composer.hint")}
        </span>
        {streaming ? (
          <button type="button" className="secondary" onClick={onStop}>
            {t("composer.stop")}
          </button>
        ) : (
          <button type="submit" className="primary" disabled={!canSend}>
            {t("composer.send")}
          </button>
        )}
      </div>
    </form>
  );
}
