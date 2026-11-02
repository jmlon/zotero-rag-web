import { useTranslation } from "react-i18next";

import { useLibrary } from "../hooks/queries";
import { useNow } from "../hooks/useNow";

const STALE_MS = 2 * 24 * 3600 * 1000;

function relative(iso: string, language: string, now: number): string {
  const seconds = (new Date(iso).getTime() - now) / 1000;
  const format = new Intl.RelativeTimeFormat(language, { numeric: "auto" });
  for (const [unit, size] of [["day", 86400], ["hour", 3600], ["minute", 60]] as const) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return format.format(0, "minute");
}

/** The footer: what the library holds and how fresh it is (PRD §4.6). */
export function LibraryStatus() {
  const { t, i18n } = useTranslation();
  const { data, isError } = useLibrary();
  const now = useNow();
  if (isError) return <p className="warning">{t("footer.offline")}</p>;
  if (!data) return null;
  const name = data.name ?? t("app.title");
  if (!data.last_sync_at) return <p>{t("footer.neverSynced", { name })}</p>;
  const stale = now - new Date(data.last_sync_at).getTime() > STALE_MS;
  const problem = data.last_sync_status && !["ok", "unchanged"].includes(data.last_sync_status);
  const items = new Intl.NumberFormat(i18n.resolvedLanguage).format(data.items);
  return (
    <p>
      {t("footer.library", { name, items, when: relative(data.last_sync_at, i18n.resolvedLanguage ?? "en", now) })}
      {(stale || problem) && (
        <span className="badge warning" title={problem ? t("footer.syncProblem") : t("footer.stale")}>
          {" "}⚠ {problem ? t("footer.syncProblem") : t("footer.stale")}
        </span>
      )}
    </p>
  );
}
