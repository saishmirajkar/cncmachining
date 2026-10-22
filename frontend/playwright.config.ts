import { defineConfig } from "@playwright/test";

/**
 * End-to-end tests against a running stack:
 *   backend API on :8000 (with an RQ worker and the FreeCAD runtime) and the Vite dev server on :5173.
 * See docs/DEVELOPMENT.md. CHROMIUM_PATH can point to a preinstalled browser.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 300_000,
  expect: { timeout: 30_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173",
    viewport: { width: 1600, height: 1000 },
    screenshot: "only-on-failure",
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] } : {},
  },
});
