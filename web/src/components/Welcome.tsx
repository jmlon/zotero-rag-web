import { useTranslation } from "react-i18next";

import { useLibrary } from "../hooks/queries";

/** A new conversation: a greeting, and examples to start from. */
export function Welcome({ onExample }: { onExample: (question: string) => void }) {
  const { t } = useTranslation();
  const { data: library } = useLibrary();
  const examples = t("welcome.examples", { returnObjects: true }) as string[];
  return (
    <div className="welcome">
      <h2>{t("welcome.heading")}</h2>
      <p className="muted">{t("app.tagline")}</p>
      {library && !library.indexed ? (
        <p className="warning">{t("welcome.notIndexed")}</p>
      ) : (
        <>
          <h3>{t("welcome.examplesLabel")}</h3>
          <ul className="examples">
            {examples.map((example) => (
              <li key={example}>
                <button type="button" className="example" onClick={() => onExample(example)}>
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
