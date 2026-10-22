import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const FIXTURE = path.resolve(import.meta.dirname, "../../backend/tests/fixtures/pocketed_part.step");
const SHOTS = process.env.E2E_SCREENSHOTS;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false });
}

async function register(page: Page, email: string, org: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: "Create an organisation" }).click();
  await page.getByLabel("Organisation name").fill(org);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("correct horse battery");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

test("complete STEP-to-program workflow through the UI", async ({ page, browser }) => {
  const stamp = Date.now();
  const email = `programmer-${stamp}@example.com`;
  await register(page, email, `E2E Shop ${stamp}`);
  await shot(page, "01-dashboard-empty");

  // 1. upload
  await page.getByRole("link", { name: "New CNC Job", exact: true }).click();
  await page.getByTestId("new-project-name").fill("E2E bracket");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.getByTestId("file-input").setInputFiles(FIXTURE);

  // 2. geometry
  await expect(page.getByText("Detected features")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("Size X")).toBeVisible();
  await page.getByRole("cell", { name: "P1", exact: true }).click();
  await expect(page.getByText("Feature P1: Pocket")).toBeVisible();
  await shot(page, "02-geometry-review");
  await page.getByTestId("create-setup").click();

  // 3. machine
  await expect(page.getByText("Selected configuration")).toBeVisible();
  await page.getByRole("button", { name: /LinuxCNC 2\.9 \(example\)/ }).first().click();
  await expect(page.getByText("Machine-specific blocks (owner supplied)")).toBeVisible();
  await shot(page, "03-machine");
  await page.getByTestId("next-step").click();

  // 4. material, stock, WCS, workholding
  await page.getByTestId("orientation").selectOption("+Z");
  await page.getByTestId("material").selectOption({ label: "6061-T6 — ASTM B221 / EN AW-6061" });
  await page.getByTestId("save-setup").click();
  await expect(page.getByText("Setup preview (WCS)")).toBeVisible();
  for (const [label, v] of [["XY allowance / side", "2"], ["Top allowance", "1"], ["Bottom allowance", "8"]])
    await page.getByLabel(label).fill(v);
  await page.getByRole("button", { name: "Compute" }).click();
  await expect(page.getByTestId("stock-x")).toHaveValue("104");
  await page.getByTestId("work-offset").selectOption("G54");
  await page.getByTestId("datum").selectOption("stock_top_front_left");
  await page.getByTestId("clearance").fill("15");
  await page.getByTestId("safe").fill("3");
  await page.getByTestId("workholding").selectOption("vise");
  await page.getByTestId("vise-jaw_width").fill("150");
  await page.getByTestId("vise-jaw_height").fill("40");
  await page.getByTestId("vise-jaw_thickness").fill("20");
  await page.getByTestId("vise-clamp_depth").fill("6");
  await page.getByTestId("tolerance").fill("0.05");
  await page.getByTestId("coolant").selectOption("flood");
  await page.getByTestId("save-setup").click();
  await expect(page.getByText("All setup requirements for this step are satisfied.")).toBeVisible();
  await page.waitForTimeout(1500); // first WebGL frame (software rendering in CI)
  await shot(page, "04-setup");
  await page.getByTestId("next-step").click();

  // 5. tooling
  await page.getByRole("button", { name: "Create example library" }).click();
  await expect(page.getByText("Example tool data")).toBeVisible();
  await expect(page.getByRole("cell", { name: "T7", exact: true })).toBeVisible();
  await page.getByTestId("next-step").click();

  // 6. plan
  await page.getByTestId("generate-plan").click();
  await expect(page.getByTestId("ops-table")).toBeVisible();
  await expect(page.getByTestId("unresolved").first()).toBeVisible();
  await shot(page, "06-plan");
  await page.getByTestId("ack-unresolved").check();
  await page.getByTestId("approve-plan").click();
  await expect(page.getByText("Plan approved for CAM computation")).toBeVisible();
  await page.getByTestId("next-step").click();

  // 7. CAM
  await page.getByTestId("run-cam").click();
  await expect(page.getByText("Engineering review required").first()).toBeVisible({ timeout: 240_000 });
  await expect(page.getByRole("cell", { name: /Adaptive clear pocket P1/ })).toBeVisible();
  await shot(page, "07-cam");
  await page.getByTestId("next-step").click();

  // 8. validation and simulation
  await expect(page.getByText("Verification levels")).toBeVisible();
  await expect(page.getByTestId("finding").first()).toBeVisible();
  await shot(page, "08-validation");
  await page.getByRole("tab", { name: "Simulation" }).click();
  await expect(page.getByText("Estimated cycle time")).toBeVisible();
  await page.waitForTimeout(1500);
  await shot(page, "08b-simulation");
  await page.getByTestId("next-step").click();

  // 9. program
  await expect(page.getByTestId("gcode-viewer")).toContainText("G43 H");
  await shot(page, "09-program");
  const dl = page.waitForEvent("download");
  await page.getByTestId("export-bundle").click();
  expect((await dl).suggestedFilename()).toMatch(/_UNRELEASED\.zip$/);
  await page.getByRole("tab", { name: "Review & release" }).click();
  await expect(page.getByTestId("release")).toBeDisabled();
  await shot(page, "09b-review");

  // a second engineer reviews and releases (four-eyes rule)
  const orgHeader = await page.evaluate(() => localStorage.getItem("cnc.org"));
  const token = await page.evaluate(() => localStorage.getItem("cnc.token"));
  const reviewerEmail = `engineer-${stamp}@example.com`;
  const ctx2 = await browser.newContext();
  const p2 = await ctx2.newPage();
  await register(p2, reviewerEmail, `Reviewer home ${stamp}`);
  const add = await page.request.post("/api/organization/members", {
    data: { email: reviewerEmail, role: "engineer" },
    headers: { Authorization: `Bearer ${token}`, "X-Organization-Id": orgHeader! },
  });
  expect(add.ok()).toBeTruthy();
  await p2.evaluate((o) => localStorage.setItem("cnc.org", o), orgHeader!);
  const url = page.url();
  await p2.goto(url.replace(/\/jobs\/([^/]+)\/export/, "/jobs/$1/export"));
  await p2.getByRole("tab", { name: "Review & release" }).click();
  for (const a of ["software_checks_only", "setup_verified", "tool_data_verified", "first_article_prove_out"]) await p2.getByTestId(`ack-${a}`).check();
  await p2.getByTestId("approve-review").click();
  await expect(p2.getByText(/Last review: approved/)).toBeVisible();
  await p2.getByTestId("release").click();
  await expect(p2.getByText("Released for controlled prove-out")).toBeVisible();
  await shot(p2, "10-released");
  await ctx2.close();
});
