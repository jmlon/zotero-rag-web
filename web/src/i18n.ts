import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import es from "./locales/es.json";

export const LANGUAGES = ["en", "es"] as const;
export type Language = (typeof LANGUAGES)[number];
const STORAGE_KEY = "zrw.language";

/** The remembered choice, else the browser's language, else English. Storage can
 * be unavailable (private windows, blocked site data): never fail on it. */
export function initialLanguage(): Language {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "en" || saved === "es") return saved;
  } catch {
    // fall through
  }
  return navigator.language?.toLowerCase().startsWith("es") ? "es" : "en";
}

export function rememberLanguage(language: Language): void {
  try {
    localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // a per-browser convenience only
  }
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, es: { translation: es } },
  lng: initialLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false }, // React escapes
  returnObjects: true,
});

// <html lang> follows the language, so screen readers pronounce the page right.
i18n.on("languageChanged", (language) => {
  document.documentElement.lang = language;
});
document.documentElement.lang = i18n.resolvedLanguage ?? "en";

export default i18n;
