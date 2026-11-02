/** What `/healthz` reports, as the shell shows it. */
export type Health =
  | { state: "checking" }
  | { state: "ok"; version: string }
  | { state: "degraded"; database: string; version: string }
  | { state: "offline" };

export async function fetchHealth(fetcher: typeof fetch = fetch): Promise<Health> {
  try {
    const response = await fetcher("/healthz", { headers: { Accept: "application/json" } });
    const body = (await response.json()) as { database: string; version: string };
    return response.ok
      ? { state: "ok", version: body.version }
      : { state: "degraded", database: body.database, version: body.version };
  } catch {
    return { state: "offline" };
  }
}
