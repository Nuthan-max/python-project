import { chromium } from "patchright";

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  console.log("Navigating to RescueRoute...");
  await page.goto("http://127.0.0.1:5000", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  // 1. Click first preset (Connaught Place)
  console.log("Selecting preset Connaught Place...");
  const presetBtn = page.locator("#emergency-presets button").first();
  await presetBtn.click();
  await page.waitForTimeout(1000);

  // 2. Select Hospital destination
  console.log("Selecting hospital destination...");
  await page.waitForFunction(() => document.querySelector("#hospital-select").options.length > 1, { timeout: 6000 });
  await page.locator("#hospital-select").selectOption({ index: 1 });
  await page.waitForTimeout(500);

  // 3. Deploy Ambulance
  console.log("Deploying ambulance...");
  await page.locator("#btn-add-ambulance").click();
  await page.waitForTimeout(1000);

  // 4. Click Plan & Dispatch
  console.log("Dispatching mission...");
  await page.locator("#btn-plan").click();
  await page.waitForTimeout(4000);

  await page.screenshot({ path: "data/qa-routed-mission.png" });
  console.log("Routed mission screenshot saved to data/qa-routed-mission.png");

  // 5. Open Analytics modal
  console.log("Opening analytics modal...");
  await page.locator("#tab-btn-analytics").click();
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "data/qa-analytics-tab.png" });
  console.log("Analytics tab screenshot saved to data/qa-analytics-tab.png");

  // 6. Close Analytics & Open History modal
  console.log("Opening history modal...");
  await page.locator(".modal-close").first().click();
  await page.waitForTimeout(500);
  await page.locator("#tab-btn-history").click();
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "data/qa-history-tab.png" });
  console.log("History tab screenshot saved to data/qa-history-tab.png");

  await browser.close();
  console.log("QA test complete!");
}

main().catch(console.error);
