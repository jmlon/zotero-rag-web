import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";

import { ApiError, api } from "../api/client";
import type { ErrorCode, Filters } from "../api/types";
import { keys, useConversation, useUpdateConversation } from "../hooks/queries";
import { useAsk } from "../hooks/useAsk";
import { Composer } from "./Composer";
import { ErrorNote } from "./ErrorNote";
import { FilterPanel, activeFilterCount } from "./FilterPanel";
import { LibraryStatus } from "./LibraryStatus";
import { Sidebar } from "./Sidebar";
import { Thread } from "./Thread";
import { Welcome } from "./Welcome";

export function ChatPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const { data: conversation, error: loadError } = useConversation(id);
  const update = useUpdateConversation(id ?? "");
  const { pending, ask, stop, dismiss } = useAsk();
  const [draftFilters, setDraftFilters] = useState<Filters>({});
  const [createError, setCreateError] = useState<ErrorCode | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);

  const filters = id ? (conversation?.filters ?? {}) : draftFilters;
  const visiblePending = pending && pending.conversationId === id ? pending : null;
  const streaming = visiblePending?.status === "streaming";

  const askQuestion = async (question: string) => {
    dismiss();
    setCreateError(null);
    let conversationId = id;
    if (!conversationId) {
      try {
        const created = await api.createConversation(draftFilters);
        conversationId = created.id;
        client.setQueryData(keys.conversation(created.id), { ...created, messages: [] });
        void client.invalidateQueries({ queryKey: keys.conversations });
        setDraftFilters({});
        navigate(`/c/${created.id}`);
      } catch (error) {
        setCreateError(error instanceof ApiError ? error.code : "network");
        return;
      }
    }
    void ask(conversationId, question);
  };

  const changeFilters = (next: Filters) => {
    if (id) update.mutate({ filters: next });
    else setDraftFilters(next);
  };

  const searchEverything = async (question: string) => {
    await update.mutateAsync({ filters: {} });
    void ask(id!, question);
  };

  const count = activeFilterCount(filters);
  const title = conversation?.title || t("app.title");

  return (
    <div className={`layout${drawerOpen ? " drawer-open" : ""}`}>
      <aside id="sidebar" className="sidebar-shell">
        <Sidebar activeId={id} onNavigate={() => setDrawerOpen(false)} />
      </aside>
      {drawerOpen && <div className="scrim" aria-hidden="true" onClick={() => setDrawerOpen(false)} />}
      <main className="main">
        <header className="topbar">
          <button
            type="button"
            className="icon menu"
            aria-controls="sidebar"
            aria-expanded={drawerOpen}
            aria-label={drawerOpen ? t("sidebar.close") : t("sidebar.open")}
            onClick={() => setDrawerOpen((open) => !open)}
          >
            ☰
          </button>
          <h1>{title}</h1>
          <button
            type="button"
            className={count ? "secondary active" : "secondary"}
            aria-expanded={filtersOpen}
            aria-controls="filters"
            onClick={() => setFiltersOpen((open) => !open)}
          >
            {count ? t("filters.count", { count }) : t("filters.label")}
          </button>
        </header>
        {filtersOpen && (
          <div id="filters">
            <FilterPanel filters={filters} onChange={changeFilters} />
          </div>
        )}
        <div className="scroll">
          {id && loadError ? (
            <ErrorNote code={loadError instanceof ApiError ? loadError.code : "network"} />
          ) : id && conversation && (conversation.messages.length || visiblePending) ? (
            <Thread
              messages={conversation.messages}
              pending={visiblePending}
              filtered={count > 0}
              onRetry={(question) => void askQuestion(question)}
              onSearchEverything={(question) => void searchEverything(question)}
            />
          ) : (
            <Welcome onExample={(question) => void askQuestion(question)} />
          )}
          {createError && <ErrorNote code={createError} />}
        </div>
        <Composer streaming={streaming} onAsk={(q) => void askQuestion(q)} onStop={stop} />
        <footer className="status">
          <LibraryStatus />
        </footer>
      </main>
    </div>
  );
}
