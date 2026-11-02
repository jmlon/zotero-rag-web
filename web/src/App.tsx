import { useEffect, useState } from "react";

import { fetchHealth, type Health } from "./health";

/** The M1 shell: the page the chat (M4) will grow into, and a live health line. */
export function App() {
  const [health, setHealth] = useState<Health>({ state: "checking" });

  useEffect(() => {
    let cancelled = false;
    fetchHealth().then((result) => {
      if (!cancelled) setHealth(result);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="shell">
      <header>
        <h1>Zotero RAG</h1>
        <p className="tagline">Ask your team's Zotero library. Every answer is cited.</p>
      </header>
      <main>
        <p className="placeholder">The chat arrives in a later milestone.</p>
      </main>
      <footer>
        <HealthLine health={health} />
      </footer>
    </div>
  );
}

function HealthLine({ health }: { health: Health }) {
  switch (health.state) {
    case "checking":
      return <span role="status">Checking the server…</span>;
    case "ok":
      return <span role="status">Server and database are up · v{health.version}</span>;
    case "degraded":
      return (
        <span role="status" className="warning">
          Server is up, database is {health.database} · v{health.version}
        </span>
      );
    case "offline":
      return (
        <span role="status" className="warning">
          The server cannot be reached.
        </span>
      );
  }
}
