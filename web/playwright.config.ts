import { defineConfig, devices } from "@playwright/test";

const PORT = 8765;

/** Browser tests against tests/e2e/server.py: the real backend with fake models. */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, grepInvert: /@phone/ },
    { name: "phone", use: { ...devices["Pixel 7"] }, grep: /@phone/ },
  ],
  webServer: {
    command: `uv run python tests/e2e/server.py --port ${PORT}`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/healthz`,
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
  },
});
