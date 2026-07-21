import { defineConfig, type PlaywrightTestConfig } from "@playwright/test";

const widths = [320, 375, 768, 1024, 1440] as const;
const themes = ["light", "dark"] as const;

const projects: NonNullable<PlaywrightTestConfig["projects"]> = widths.flatMap((width) =>
  themes.map((theme) => ({
    name: `${width}-${theme}`,
    metadata: { width, theme },
    use: {
      viewport: { width, height: width <= 375 ? 812 : 900 },
      colorScheme: theme,
    },
  })),
);

export default defineConfig({
  testDir: "./e2e",
  outputDir: "test-results",
  snapshotPathTemplate: "{testDir}/__snapshots__/{platform}/{projectName}/{arg}{ext}",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI
    ? [["github"], ["html", { outputFolder: "playwright-report", open: "never" }]]
    : [["list"]],
  expect: {
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      maxDiffPixelRatio: 0.03,
    },
  },
  use: {
    baseURL: "http://127.0.0.1:4173",
    browserName: "chromium",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects,
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173/",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
