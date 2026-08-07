import { defineConfig } from '@playwright/test';

const baseURL = process.env.MTERMINALS_E2E_BASE_URL
  || 'http://127.0.0.1:5500/dist/Dashboard/DashboardPro.html';

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    headless: true,
    channel: process.env.MTERMINALS_E2E_CHANNEL || 'chrome',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // Keep the foundation dependency-light: traces and screenshots provide
    // deterministic diagnostics without requiring Playwright's optional
    // FFmpeg download on every developer machine.
    video: 'off',
  },
});
