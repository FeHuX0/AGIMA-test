import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:8765", headless: true, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { browserName: "chromium", channel: "chromium" } }],
  webServer: {
    command: ".venv/bin/python -m tests.e2e.server",
    url: "http://127.0.0.1:8765/api/ready",
    timeout: 60000,
    reuseExistingServer: false,
  },
});
