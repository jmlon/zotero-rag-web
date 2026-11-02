import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import type { ConversationSummary } from "../api/types";
import { LANGUAGES, type Language, rememberLanguage } from "../i18n";
import { useConversations, useDeleteConversation } from "../hooks/queries";
import { useNow } from "../hooks/useNow";

const DAY = 24 * 3600 * 1000;

function group(conversations: ConversationSummary[], now: number) {
  const groups: Record<"today" | "week" | "older", ConversationSummary[]> = {
    today: [],
    week: [],
    older: [],
  };
  const midnight = new Date(now).setHours(0, 0, 0, 0);
  for (const conversation of conversations) {
    const at = new Date(conversation.updated_at).getTime();
    if (at >= midnight) groups.today.push(conversation);
    else if (at >= midnight - 7 * DAY) groups.week.push(conversation);
    else groups.older.push(conversation);
  }
  return groups;
}

export function Sidebar({ activeId, onNavigate }: { activeId?: string; onNavigate: () => void }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { data: conversations = [] } = useConversations();
  const remove = useDeleteConversation();
  const now = useNow();
  const groups = group(conversations, now);

  const setLanguage = (language: Language) => {
    void i18n.changeLanguage(language);
    rememberLanguage(language);
  };

  return (
    <nav className="sidebar" aria-label={t("sidebar.conversations")}>
      <Link to="/" className="button primary new-conversation" onClick={onNavigate}>
        + {t("sidebar.new")}
      </Link>
      {conversations.length === 0 && <p className="muted">{t("sidebar.empty")}</p>}
      {(["today", "week", "older"] as const).map((name) =>
        groups[name].length ? (
          <section key={name}>
            <h2>{t(`sidebar.${name}`)}</h2>
            <ul>
              {groups[name].map((conversation) => (
                <li key={conversation.id} className={conversation.id === activeId ? "active" : ""}>
                  <Link
                    to={`/c/${conversation.id}`}
                    aria-current={conversation.id === activeId ? "page" : undefined}
                    onClick={onNavigate}
                  >
                    {conversation.title || t("sidebar.untitled")}
                  </Link>
                  <button
                    type="button"
                    className="icon"
                    aria-label={`${t("sidebar.delete")}: ${conversation.title || t("sidebar.untitled")}`}
                    onClick={() => {
                      if (!window.confirm(t("sidebar.confirmDelete"))) return;
                      remove.mutate(conversation.id, {
                        onSuccess: () => conversation.id === activeId && navigate("/"),
                      });
                    }}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : null,
      )}
      <div className="language" role="group" aria-label={t("language.label")}>
        {LANGUAGES.map((language) => (
          <button
            key={language}
            type="button"
            aria-pressed={i18n.resolvedLanguage === language}
            onClick={() => setLanguage(language)}
          >
            {language.toUpperCase()}
          </button>
        ))}
      </div>
    </nav>
  );
}
