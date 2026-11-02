import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Collection, Filters } from "../api/types";
import { useCollections, useLibrary, useTags } from "../hooks/queries";

export function activeFilterCount(filters: Filters): number {
  return (
    (filters.collection ? 1 : 0) +
    (filters.year_min != null || filters.year_max != null ? 1 : 0) +
    (filters.tags?.length ? 1 : 0)
  );
}

/** Indentation for the tree inside a native <select>. */
function depth(collection: Collection): number {
  return collection.path.split("/").length - 1;
}

export function FilterPanel({ filters, onChange }: { filters: Filters; onChange: (f: Filters) => void }) {
  const { t } = useTranslation();
  const ids = useId();
  const { data: collections = [] } = useCollections();
  const { data: library } = useLibrary();
  const [tagInput, setTagInput] = useState("");
  const { data: suggestions = [] } = useTags(tagInput, tagInput.length > 0);

  const set = (change: Partial<Filters>) => {
    const next: Filters = { ...filters, ...change };
    for (const key of Object.keys(next) as (keyof Filters)[]) {
      if (next[key] === undefined || (Array.isArray(next[key]) && !next[key]?.length)) delete next[key];
    }
    onChange(next);
  };
  const year = (value: string) => (value === "" ? undefined : Number(value));
  const tags = filters.tags ?? [];
  const addTag = (tag: string) => {
    const clean = tag.trim();
    if (clean && !tags.includes(clean)) set({ tags: [...tags, clean] });
    setTagInput("");
  };

  return (
    <fieldset className="filter-panel">
      <legend className="visually-hidden">{t("filters.label")}</legend>
      <div className="field">
        <label htmlFor={`${ids}-collection`}>{t("filters.collection")}</label>
        <select
          id={`${ids}-collection`}
          value={filters.collection ?? ""}
          onChange={(event) => set({ collection: event.target.value || undefined })}
        >
          <option value="">{t("filters.wholeLibrary")}</option>
          {collections.map((collection) => (
            <option key={collection.key} value={collection.key}>
              {"  ".repeat(depth(collection))}
              {collection.name}
            </option>
          ))}
        </select>
      </div>

      <div className="field years">
        <span className="field-label" id={`${ids}-years`}>
          {t("filters.years")}
        </span>
        <div className="year-inputs" role="group" aria-labelledby={`${ids}-years`}>
          <label>
            <span className="visually-hidden">{t("filters.from")}</span>
            <input
              type="number"
              inputMode="numeric"
              placeholder={library?.year_min ? String(library.year_min) : t("filters.from")}
              value={filters.year_min ?? ""}
              min={0}
              max={9999}
              onChange={(event) => set({ year_min: year(event.target.value) })}
            />
          </label>
          <span aria-hidden="true">–</span>
          <label>
            <span className="visually-hidden">{t("filters.to")}</span>
            <input
              type="number"
              inputMode="numeric"
              placeholder={library?.year_max ? String(library.year_max) : t("filters.to")}
              value={filters.year_max ?? ""}
              min={0}
              max={9999}
              onChange={(event) => set({ year_max: year(event.target.value) })}
            />
          </label>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={filters.include_undated ?? true}
            onChange={(event) => set({ include_undated: event.target.checked ? undefined : false })}
          />
          {t("filters.includeUndated")}
        </label>
      </div>

      <div className="field">
        <label htmlFor={`${ids}-tags`}>{t("filters.tags")}</label>
        {tags.length > 0 && (
          <ul className="tag-list">
            {tags.map((tag) => (
              <li key={tag}>
                {tag}
                <button
                  type="button"
                  aria-label={t("filters.removeTag", { tag })}
                  onClick={() => set({ tags: tags.filter((other) => other !== tag) })}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <input
          id={`${ids}-tags`}
          list={`${ids}-tag-options`}
          value={tagInput}
          placeholder={t("filters.addTag")}
          onChange={(event) => {
            const value = event.target.value;
            // Picking a datalist option fills the whole value: add it at once.
            if (suggestions.some((s) => s.tag === value)) addTag(value);
            else setTagInput(value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addTag(tagInput);
            }
          }}
        />
        <datalist id={`${ids}-tag-options`}>
          {suggestions.map((s) => (
            <option key={s.tag} value={s.tag} />
          ))}
        </datalist>
      </div>

      {activeFilterCount(filters) > 0 && (
        <button type="button" className="link-button" onClick={() => onChange({})}>
          {t("filters.clear")}
        </button>
      )}
    </fieldset>
  );
}
