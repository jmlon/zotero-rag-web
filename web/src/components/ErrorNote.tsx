import { useTranslation } from "react-i18next";

import type { ErrorCode } from "../api/types";

const RETRYABLE: ErrorCode[] = ["llm_failed", "internal", "network", "rate_limited"];

export function ErrorNote({
  code,
  retryAfterS,
  onRetry,
}: {
  code: ErrorCode;
  retryAfterS?: number;
  onRetry?: () => void;
}) {
  const { t } = useTranslation();
  const minutes = Math.max(1, Math.ceil((retryAfterS ?? 60) / 60));
  return (
    <div className="message error-note" role="alert">
      <p>{t(`errors.${code}`, { minutes })}</p>
      {onRetry && RETRYABLE.includes(code) && (
        <button type="button" className="ghost" onClick={onRetry}>
          {t("answer.retry")}
        </button>
      )}
    </div>
  );
}
